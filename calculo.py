"""
Cálculo de custo e preço de impressão a partir do G-code gerado.

O PrusaSlicer grava as estatísticas (tempo e filamento) no fim do G-code,
junto com a lista de parâmetros usados. Este módulo lê o início e o fim do
arquivo e monta um orçamento considerando:

- filamento consumido;
- energia elétrica;
- desgaste/manutenção do equipamento (por hora de impressão);
- acabamento e mão de obra (por hora + valor fixo por peça);
- embalagem (por peça).

A interface só precisa pedir a margem de lucro; os demais valores vêm com
padrões editáveis em CUSTOS_PADRAO.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

# Usado apenas quando o G-code não informa o peso do filamento.
DIAMETRO_FILAMENTO_MM = 1.75
DENSIDADE_PLA_G_CM3 = 1.24

# O cabeçalho é pequeno, mas o resumo do PrusaSlicer fica no rodapé.
MAX_LINHAS_INICIO = 500
MAX_LINHAS_FIM = 3000
TAMANHO_MAXIMO_GCODE_MB = 200

# Usado quando o G-code não traz o tempo estimado.
TEMPO_PADRAO_H = 1.5


class ErroCalculo(RuntimeError):
    """Erro amigável exibido na aba Calc."""


@dataclass(frozen=True)
class DadosImpressao:
    """Dados extraídos do G-code (ou informados pelo usuário)."""

    arquivo: Path
    horas: float
    filamento_mm: float
    filamento_g: float
    tempo_encontrado: bool = True


@dataclass(frozen=True)
class Custos:
    """Valores unitários usados no orçamento (todos em reais)."""

    preco_filamento_kg: float = 100.0
    preco_kwh: float = 0.92
    consumo_w: float = 60.0
    desgaste_por_hora: float = 1.5
    mao_obra_por_hora: float = 15.0
    acabamento_por_peca: float = 2.0
    embalagem_por_peca: float = 1.5


CUSTOS_PADRAO = Custos()


@dataclass(frozen=True)
class Orcamento:
    """Resultado completo do cálculo."""

    dados: DadosImpressao
    custos: Custos
    margem_percentual: float
    filamento: float
    energia: float
    desgaste: float
    mao_obra: float
    acabamento: float
    embalagem: float
    custo_total: float
    preco_final: float
    lucro: float

    def linhas(self) -> list[tuple[str, float]]:
        """Custos em ordem de exibição na interface."""
        return [
            ("Filamento", self.filamento),
            ("Energia elétrica", self.energia),
            ("Desgaste/manutenção", self.desgaste),
            ("Mão de obra (tempo)", self.mao_obra),
            ("Acabamento", self.acabamento),
            ("Embalagem", self.embalagem),
        ]


# Ordem de preferência: o formato nativo do PrusaSlicer vem primeiro.
_TEMPO_PADROES = (
    re.compile(r"estimated printing time[^\n:=]*[:=]\s*(.+)", re.IGNORECASE),
    re.compile(r"total estimated time[^\n:=]*[:=]\s*(.+)", re.IGNORECASE),
    re.compile(r"(?:total\s+)?printing time[^\n:=]*[:=]\s*(.+)", re.IGNORECASE),
    re.compile(r"\bprint time\b[^\n:=]*[:=]\s*(.+)", re.IGNORECASE),
    re.compile(r"\bTIME\s*[:=]\s*([\d.,]+)\s*(?:s|seg)?\b", re.IGNORECASE),
)
_FILAMENTO_MM_PADROES = (
    re.compile(r"(?:total\s+)?filament used\s*\[mm\]\s*[:=]\s*([\d.,]+)", re.IGNORECASE),
    re.compile(r"(?:total\s+)?filament used\s*[:=]\s*([\d.,]+)\s*mm", re.IGNORECASE),
)
_FILAMENTO_G_PADROES = (
    re.compile(r"(?:total\s+)?filament used\s*\[g\]\s*[:=]\s*([\d.,]+)", re.IGNORECASE),
)


def _numero(texto: str) -> float:
    return float(texto.replace(",", "."))


def _horas(texto: str) -> float:
    """Converte '2d 1h 5m 24s', '01:30:00', '30m' ou '38846' em horas."""
    texto = texto.strip()
    total = 0.0
    for padrao, fator in (
        (r"(\d+)\s*d", 86400.0),
        (r"(\d+)\s*h", 3600.0),
        (r"(\d+)\s*m(?!s)", 60.0),
        (r"(\d+)\s*s", 1.0),
    ):
        encontrado = re.search(padrao, texto, re.IGNORECASE)
        if encontrado:
            total += int(encontrado.group(1)) * fator
    if total <= 0 and ":" in texto:
        partes = [float(p) for p in texto.split(":")]
        fatores = [3600.0, 60.0, 1.0][-len(partes) :]
        total = sum(parte * fator for parte, fator in zip(partes, fatores))
    if total <= 0:
        # Alguns slicers gravam apenas os segundos (ex.: Cura ';TIME:38846').
        try:
            total = _numero(texto)
        except ValueError:
            pass
    return total / 3600.0


def _linhas_gcode(arquivo: Path) -> tuple[list[str], list[str]]:
    """Lê as primeiras e as últimas linhas sem carregar o arquivo inteiro."""
    with arquivo.open("rb") as stream:
        tamanho = stream.seek(0, os.SEEK_END)
        if tamanho > TAMANHO_MAXIMO_GCODE_MB * 1024 * 1024:
            raise ErroCalculo(
                f"G-code muito grande ({tamanho // (1024 * 1024)} MB). "
                f"Limite: {TAMANHO_MAXIMO_GCODE_MB} MB."
            )

        inicio: list[bytes] = []
        stream.seek(0)
        for _ in range(MAX_LINHAS_INICIO):
            linha = stream.readline()
            if not linha:
                break
            inicio.append(linha)

        fim: list[bytes] = []
        bloco = 65536
        posicao = tamanho
        sobra = b""
        while posicao > 0 and len(fim) < MAX_LINHAS_FIM:
            quantidade = min(bloco, posicao)
            posicao -= quantidade
            stream.seek(posicao)
            pedaco = stream.read(quantidade)
            partes = (pedaco + sobra).splitlines()
            if not partes:
                sobra = pedaco + sobra
                continue
            # O primeiro elemento é o trecho de uma linha que começa no bloco
            # anterior (mais à esquerda); ele será completado na próxima volta.
            sobra = partes.pop(0)
            # 'fim' já contém linhas posteriores; as novas vêm antes delas.
            fim = partes + fim
            if len(fim) > MAX_LINHAS_FIM:
                fim = fim[-MAX_LINHAS_FIM:]
        if posicao == 0 and sobra:
            fim.insert(0, sobra)
            if len(fim) > MAX_LINHAS_FIM:
                fim = fim[-MAX_LINHAS_FIM:]

    decodificar = lambda linhas: [
        linha.decode("utf-8", errors="replace") for linha in linhas
    ]
    return decodificar(inicio), decodificar(fim)


def _procurar(padroes, linhas: list[str], excluir: tuple[str, ...] = ()) -> str | None:
    for padrao in padroes:
        for linha in linhas:
            if any(palavra in linha.lower() for palavra in excluir):
                continue
            encontrado = padrao.search(linha)
            if encontrado:
                return encontrado.group(1)
    return None


def _procurar_positivo(padroes, linhas: list[str]) -> float:
    """Retorna o primeiro valor > 0, preferindo o total ao de uma extrusora."""
    for padrao in padroes:
        normais: list[float] = []
        totais: list[float] = []
        for linha in linhas:
            encontrado = padrao.search(linha)
            if not encontrado:
                continue
            try:
                valor = _numero(encontrado.group(1))
            except ValueError:
                continue
            if valor <= 0:
                continue
            if "total" in encontrado.group(0).lower():
                totais.append(valor)
            else:
                normais.append(valor)
        if normais:
            return normais[0]
        if totais:
            return totais[0]
    return 0.0


def extrair_dados_gcode(
    caminho: str | Path, horas_padrao: float | None = None
) -> DadosImpressao:
    """
    Lê o início e o rodapé do G-code para extrair tempo e filamento usado.

    Se o tempo não estiver no arquivo, usa ``horas_padrao`` (em horas) e
    marca ``tempo_encontrado=False`` para a interface avisar que o valor é
    editável.
    """
    arquivo = Path(caminho).expanduser()
    if not arquivo.is_file():
        raise ErroCalculo(f"Arquivo G-code não encontrado: {arquivo}")

    try:
        inicio, fim = _linhas_gcode(arquivo)
    except OSError as exc:
        raise ErroCalculo(f"Não foi possível ler o G-code: {exc}") from exc

    linhas = inicio + fim
    texto_tempo = _procurar(
        _TEMPO_PADROES, linhas, excluir=("first layer", "primeira camada")
    )
    horas = _horas(texto_tempo) if texto_tempo else 0.0
    tempo_encontrado = horas > 0
    if not tempo_encontrado:
        if horas_padrao is None:
            raise ErroCalculo(
                "Não encontrei o tempo estimado no G-code e não recebi um valor "
                "padrão. Informe o tempo na aba Calc."
            )
        try:
            horas = float(horas_padrao)
        except (TypeError, ValueError) as exc:
            raise ErroCalculo("Tempo padrão inválido. Informe as horas na aba Calc.") from exc
        if not math.isfinite(horas) or horas <= 0:
            raise ErroCalculo("O tempo padrão deve ser maior que zero.")

    filamento_mm = _procurar_positivo(_FILAMENTO_MM_PADROES, linhas)
    filamento_g = _procurar_positivo(_FILAMENTO_G_PADROES, linhas)
    if filamento_g <= 0 and filamento_mm > 0:
        area_mm2 = math.pi * (DIAMETRO_FILAMENTO_MM / 2) ** 2
        volume_cm3 = filamento_mm * area_mm2 / 1000.0
        filamento_g = volume_cm3 * DENSIDADE_PLA_G_CM3
    if filamento_g <= 0:
        raise ErroCalculo(
            "Não encontrei o filamento usado no início nem no fim do G-code. "
            "Ative 'Verbose G-code' no PrusaSlicer e fatie novamente."
        )

    return DadosImpressao(
        arquivo=arquivo,
        horas=horas,
        filamento_mm=filamento_mm,
        filamento_g=filamento_g,
        tempo_encontrado=tempo_encontrado,
    )


def calcular_orcamento(
    dados: DadosImpressao,
    margem_percentual: float,
    custos: Custos = CUSTOS_PADRAO,
) -> Orcamento:
    """Calcula custos, preço final e lucro para a margem informada."""
    try:
        margem = float(margem_percentual)
    except (TypeError, ValueError) as exc:
        raise ErroCalculo("Informe uma margem de lucro válida em porcentagem.") from exc
    if not math.isfinite(margem) or margem < 0 or margem > 1000:
        raise ErroCalculo("A margem deve estar entre 0 e 1000 %.")

    filamento = dados.filamento_g / 1000.0 * custos.preco_filamento_kg
    energia = dados.horas * custos.consumo_w / 1000.0 * custos.preco_kwh
    desgaste = dados.horas * custos.desgaste_por_hora
    mao_obra = dados.horas * custos.mao_obra_por_hora
    custo_total = (
        filamento
        + energia
        + desgaste
        + mao_obra
        + custos.acabamento_por_peca
        + custos.embalagem_por_peca
    )
    preco_final = custo_total * (1.0 + margem / 100.0)

    return Orcamento(
        dados=dados,
        custos=custos,
        margem_percentual=margem,
        filamento=filamento,
        energia=energia,
        desgaste=desgaste,
        mao_obra=mao_obra,
        acabamento=custos.acabamento_por_peca,
        embalagem=custos.embalagem_por_peca,
        custo_total=custo_total,
        preco_final=preco_final,
        lucro=preco_final - custo_total,
    )


def com_custos(custos: Custos, **alteracoes: float) -> Custos:
    """Cria uma cópia dos custos com os campos informados alterados."""
    return replace(custos, **alteracoes)
