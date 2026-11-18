"""
Núcleo de conversão de modelos 3MF/STL para G-code da Easythreed K7.

Este módulo não depende de interface gráfica. Ele é usado por:
- app.py (interface ttkbootstrap);
- este próprio arquivo via linha de comando;
- testes automatizados.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import trimesh

# ---------------------------------------------------------------------------
# Limites da mesa e segurança
# ---------------------------------------------------------------------------
LIMITE_SEGURANCA_MM = 95.0
LARGURA_MESA_MM = 100.0
PROFUNDIDADE_MESA_MM = 100.0
ALTURA_MESA_MM = 100.0

# Proteções contra travamentos: arquivos gigantes e saída interminável do
# fatiador eram as principais causas de consumo de RAM/CPU sem controle.
TAMANHO_MAXIMO_ARQUIVO_MB = 200
MAX_FACES = 2_000_000
MAX_LINHAS_LOG = 200
TIMEOUT_FATIAMENTO_S = 300.0
QUANTIDADE_MAXIMA = 20
ESPACAMENTO_COPIAS_MM = 2.0


def _pasta_recursos() -> Path:
    """Pasta onde o .ini vem embutido dentro do executável (temporária)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def _pasta_dados_usuario() -> Path:
    """Pasta estável do usuário: não muda a cada execução do .exe."""
    if getattr(sys, "frozen", False):
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ConversorK7"
    return Path(__file__).resolve().parent


CONFIG_EMBUTIDO = _pasta_recursos() / "k7_config.ini"
CONFIG_PADRAO = _pasta_dados_usuario() / "k7_config.ini"
PRUSA_CLI_PADRAO = Path(r"C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer-console.exe")

CANDIDATOS_PRSUSA_CLI = (
    PRUSA_CLI_PADRAO,
    Path(r"C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer.exe"),
    Path(r"C:\Program Files (x86)\Prusa3D\PrusaSlicer\prusa-slicer-console.exe"),
    Path("/usr/bin/prusa-slicer"),
    Path("/Applications/PrusaSlicer.app/Contents/MacOS/PrusaSlicer"),
)

# Tipos de suporte oferecidos na interface. Cada opção sobrescreve apenas
# estas chaves num .ini temporário, sem alterar o perfil do usuário.
TIPOS_SUPORTE: dict[str, dict[str, str]] = {
    "Sem suporte": {
        "support_material": "0",
        "support_material_auto": "0",
    },
    "Normal - Grade": {
        "support_material": "1",
        "support_material_auto": "1",
        "support_material_style": "grid",
    },
    "Normal - Linhas": {
        "support_material": "1",
        "support_material_auto": "1",
        "support_material_style": "lines",
    },
    "Normal - Quadrados": {
        "support_material": "1",
        "support_material_auto": "1",
        "support_material_style": "squares",
    },
    "Normal - Colmeia": {
        "support_material": "1",
        "support_material_auto": "1",
        "support_material_style": "honeycomb",
    },
    "Normal - Ajustado (snug)": {
        "support_material": "1",
        "support_material_auto": "1",
        "support_material_style": "snug",
    },
    "Árvore - Orgânico": {
        "support_material": "1",
        "support_material_auto": "1",
        "support_material_style": "organic",
        "support_material_angling": "45",
    },
    "Árvore - Orgânico reforçado": {
        "support_material": "1",
        "support_material_auto": "1",
        "support_material_style": "organic",
        "support_material_angling": "35",
        "support_material_enforce_layers": "2",
    },
    "Árvore - Orgânico econômico": {
        "support_material": "1",
        "support_material_auto": "1",
        "support_material_style": "organic",
        "support_material_angling": "60",
        "support_material_threshold": "55",
    },
}
SUPORTE_PADRAO = "Normal - Grade"

# Aplicado somente quando a opção "Suportes mais finos" está ligada e o tipo
# escolhido realmente gera suporte. Vale para suporte normal e para árvore.
SUPORTE_FINO_PADRAO_MM = 0.3
SUPORTE_FINO_MIN_MM = 0.15
SUPORTE_FINO_MAX_MM = 0.5

LogFn = Callable[[str, str], None]


def _sem_log(_nivel: str, _mensagem: str) -> None:
    """Callback de log padrão (silencioso)."""
    return None


class ErroConversao(RuntimeError):
    """Erro de conversão já formatado para ser exibido ao usuário."""


def validar_suporte_fino_mm(valor: float, log: LogFn = _sem_log) -> float:
    """Valida a espessura desejada para o suporte fino."""
    try:
        espessura = float(valor)
    except (TypeError, ValueError) as exc:
        raise ErroConversao("Informe uma espessura válida para o suporte fino.") from exc
    if not math.isfinite(espessura):
        raise ErroConversao("A espessura do suporte fino deve ser um número válido.")

    arredondada = round(espessura, 2)
    limitado = min(max(arredondada, SUPORTE_FINO_MIN_MM), SUPORTE_FINO_MAX_MM)
    if limitado != arredondada:
        log(
            "warn",
            f"Espessura de suporte {espessura:g} mm ajustada para {limitado:g} mm "
            f"(intervalo: {SUPORTE_FINO_MIN_MM:g} a {SUPORTE_FINO_MAX_MM:g} mm).",
        )
    return limitado


def parametros_suporte_fino(espessura_mm: float) -> dict[str, str]:
    """Monta as chaves do PrusaSlicer a partir da espessura escolhida."""
    espessura = validar_suporte_fino_mm(espessura_mm)
    return {
        "support_material_extrusion_width": f"{espessura:g}",
        "support_material_interface_extrusion_width": f"{espessura:g}",
        # PrusaSlicer exige ponta >= largura e ramo >= 2x a largura.
        "support_tree_branch_diameter": f"{round(espessura * 4, 2):g}",
        "support_tree_tip_diameter": f"{round(espessura * 2, 2):g}",
    }


@dataclass(frozen=True)
class AnaliseMalha:
    """Medidas da peça antes e depois do ajuste."""

    dimensoes_originais: tuple[float, float, float]
    dimensoes_finais: tuple[float, float, float]
    fator_escala: float
    medida_aplicada: float
    partes: int
    quantidade: int = 1
    colunas: int = 1
    linhas: int = 1


@dataclass(frozen=True)
class ResultadoConversao:
    """Resultado de uma conversão concluída com sucesso."""

    arquivo_entrada: Path
    arquivo_gcode: Path
    analise: AnaliseMalha


def localizar_prusa_cli() -> Path | None:
    """Procura o executável do PrusaSlicer nos locais mais comuns e no PATH."""
    for candidato in CANDIDATOS_PRSUSA_CLI:
        if candidato.is_file():
            return candidato

    encontrado = shutil.which("prusa-slicer-console") or shutil.which("prusa-slicer")
    return Path(encontrado) if encontrado else None


def validar_medida(medida_mm: float, log: LogFn = _sem_log) -> float:
    """Converte e valida a medida desejada, aplicando a trava de segurança."""
    try:
        medida = float(medida_mm)
    except (TypeError, ValueError) as exc:
        raise ErroConversao("Informe um tamanho máximo válido em milímetros.") from exc

    if not math.isfinite(medida) or medida <= 0:
        raise ErroConversao("O tamanho máximo deve ser maior que zero.")

    if medida > LIMITE_SEGURANCA_MM:
        log(
            "warn",
            f"Medida {medida:g} mm acima do limite; aplicando "
            f"{LIMITE_SEGURANCA_MM:g} mm por segurança da mesa K7.",
        )
        medida = LIMITE_SEGURANCA_MM

    return medida


def validar_quantidade(quantidade: int, log: LogFn = _sem_log) -> int:
    """Valida quantas cópias cabem no limite de segurança de processamento."""
    try:
        valor = int(quantidade)
    except (TypeError, ValueError) as exc:
        raise ErroConversao("Informe uma quantidade válida de peças.") from exc
    if valor < 1:
        raise ErroConversao("A quantidade deve ser pelo menos 1.")
    if valor > QUANTIDADE_MAXIMA:
        log(
            "warn",
            f"Quantidade {valor} acima do limite; usando {QUANTIDADE_MAXIMA} para "
            "evitar travamento.",
        )
        valor = QUANTIDADE_MAXIMA
    return valor


def _formatar_dimensoes(dimensoes: tuple[float, float, float]) -> str:
    return " x ".join(f"{valor:.2f}" for valor in dimensoes)


def _melhor_layout(
    largura: float, profundidade: float, quantidade: int
) -> tuple[int, int, float]:
    """Escolhe a grade que permite a maior escala uniforme dentro dos 95 mm."""
    melhor: tuple[int, int, float] = (1, quantidade, 0.0)
    for colunas in range(1, quantidade + 1):
        linhas = math.ceil(quantidade / colunas)
        # O espaçamento é aplicado depois da escala, então ele não pode ser
        # multiplicado pelo fator: reservamos o espaço fixo antes de dividir.
        livre_x = LIMITE_SEGURANCA_MM - (colunas - 1) * ESPACAMENTO_COPIAS_MM
        livre_y = LIMITE_SEGURANCA_MM - (linhas - 1) * ESPACAMENTO_COPIAS_MM
        escala = min(livre_x / (colunas * largura), livre_y / (linhas * profundidade))
        if escala > melhor[2]:
            melhor = (colunas, linhas, escala)
    return melhor


def _distribuir_copias(
    malha: trimesh.Trimesh, quantidade: int, colunas: int, linhas: int
) -> trimesh.Trimesh:
    """Posiciona as cópias em grade centralizada, com espaçamento entre elas."""
    largura, profundidade, _altura = (float(v) for v in malha.extents)
    profundidade_total = (
        linhas * profundidade + (linhas - 1) * ESPACAMENTO_COPIAS_MM
    )
    copias: list[trimesh.Trimesh] = []
    indice = 0
    for linha in range(linhas):
        nesta_linha = min(colunas, quantidade - indice)
        if nesta_linha <= 0:
            break
        largura_linha = (
            nesta_linha * largura + (nesta_linha - 1) * ESPACAMENTO_COPIAS_MM
        )
        x_inicial = -largura_linha / 2 + largura / 2
        y = -profundidade_total / 2 + profundidade / 2 + linha * (
            profundidade + ESPACAMENTO_COPIAS_MM
        )
        for coluna in range(nesta_linha):
            copia = malha.copy()
            copia.apply_translation(
                [
                    x_inicial + coluna * (largura + ESPACAMENTO_COPIAS_MM),
                    y,
                    0.0,
                ]
            )
            copias.append(copia)
            indice += 1
    return trimesh.util.concatenate(copias)


def _carregar_e_preparar(
    caminho: Path, medida: float, log: LogFn, quantidade: int = 1
) -> tuple[trimesh.Trimesh, AnaliseMalha]:
    """Carrega a malha, distribui as cópias e centraliza na mesa da K7."""
    quantidade = validar_quantidade(quantidade, log)
    log("info", f"Carregando {caminho.name}...")

    try:
        tamanho_mb = caminho.stat().st_size / (1024 * 1024)
    except OSError as exc:
        raise ErroConversao(f"Não foi possível ler '{caminho.name}': {exc}") from exc
    if tamanho_mb > TAMANHO_MAXIMO_ARQUIVO_MB:
        raise ErroConversao(
            f"Arquivo muito grande ({tamanho_mb:.0f} MB). O limite é "
            f"{TAMANHO_MAXIMO_ARQUIVO_MB} MB para evitar travamento do computador."
        )

    try:
        cena = trimesh.load(str(caminho))
    except Exception as exc:  # trimesh levanta vários tipos de exceção
        raise ErroConversao(f"Não foi possível ler '{caminho.name}': {exc}") from exc

    if isinstance(cena, trimesh.Scene):
        if len(cena.geometry) == 0:
            raise ErroConversao("O arquivo não contém nenhuma geometria.")
        partes = len(cena.geometry)
        total_faces = sum(len(geometria.faces) for geometria in cena.geometry.values())
        if total_faces > MAX_FACES:
            raise ErroConversao(
                f"Modelo com {total_faces:,} triângulos é pesado demais "
                f"(limite: {MAX_FACES:,}). Simplifique o modelo antes de converter."
            )
        malha = cena.dump(concatenate=True)
    elif isinstance(cena, trimesh.Trimesh):
        partes = 1
        if len(cena.faces) > MAX_FACES:
            raise ErroConversao(
                f"Modelo com {len(cena.faces):,} triângulos é pesado demais "
                f"(limite: {MAX_FACES:,}). Simplifique o modelo antes de converter."
            )
        malha = cena
    else:
        raise ErroConversao("Formato de malha não suportado pelo trimesh.")

    if malha is None or malha.is_empty:
        raise ErroConversao("A malha carregada está vazia.")

    dimensoes_originais = tuple(float(v) for v in malha.extents)
    maior_lado = max(dimensoes_originais)
    if not math.isfinite(maior_lado) or maior_lado <= 0:
        raise ErroConversao("A malha não possui dimensões válidas.")

    colunas, linhas, escala_grade = 1, 1, math.inf
    if quantidade > 1:
        colunas, linhas, escala_grade = _melhor_layout(
            dimensoes_originais[0], dimensoes_originais[1], quantidade
        )

    # O tamanho escolhido é o teto; a grade pode reduzir a escala para que
    # todas as cópias caibam na área útil de 95 mm.
    fator_escala = min(medida / maior_lado, escala_grade)

    total_faces = len(malha.faces) * quantidade
    if total_faces > MAX_FACES:
        raise ErroConversao(
            f"{quantidade} cópia(s) somam {total_faces:,} triângulos e passam do "
            f"limite de {MAX_FACES:,}. Reduza a quantidade ou simplifique o modelo."
        )

    malha.apply_scale(fator_escala)
    if quantidade > 1:
        malha = _distribuir_copias(malha, quantidade, colunas, linhas)

    minimo, maximo = malha.bounds
    centro_x = (minimo[0] + maximo[0]) / 2
    centro_y = (minimo[1] + maximo[1]) / 2
    base_z = minimo[2]

    malha.apply_translation(
        [
            LARGURA_MESA_MM / 2 - centro_x,
            PROFUNDIDADE_MESA_MM / 2 - centro_y,
            -base_z,
        ]
    )

    dimensoes_finais = tuple(float(v) for v in malha.extents)
    analise = AnaliseMalha(
        dimensoes_originais=dimensoes_originais,
        dimensoes_finais=dimensoes_finais,
        fator_escala=fator_escala,
        medida_aplicada=medida,
        partes=partes,
        quantidade=quantidade,
        colunas=colunas,
        linhas=linhas,
    )

    log("info", f"Dimensões originais: {_formatar_dimensoes(dimensoes_originais)} mm ({partes} parte(s))")
    if quantidade > 1:
        log(
            "info",
            f"{quantidade} cópias em grade {colunas}x{linhas}, espaçamento de "
            f"{ESPACAMENTO_COPIAS_MM:g} mm; escala automática aplicada para caber.",
        )
    log("info", f"Escala {fator_escala:.4f}x -> {_formatar_dimensoes(dimensoes_finais)} mm")
    log("info", "Posicionamento: centro do conjunto em (50, 50) mm e base em Z = 0 mm")

    return malha, analise


def analisar_arquivo(
    arquivo: str | Path,
    medida_maxima_mm: float,
    log: LogFn = _sem_log,
    *,
    quantidade: int = 1,
) -> AnaliseMalha:
    """Só lê e mede a peça (usado pela prévia da interface)."""
    caminho = Path(arquivo).expanduser()
    if not caminho.is_file():
        raise ErroConversao(f"Arquivo não encontrado: {caminho}")

    medida = validar_medida(medida_maxima_mm, log)
    return _carregar_e_preparar(caminho, medida, log, quantidade)[1]


def _gravar_ini_com_suporte(
    config: Path, parametros: dict[str, str], destino: Path
) -> None:
    """Copia o perfil e substitui só as chaves de suporte."""
    try:
        linhas = config.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise ErroConversao(f"Não foi possível ler o perfil {config.name}: {exc}") from exc

    chaves = set(parametros)
    resultado = [
        linha
        for linha in linhas
        if not ("=" in linha and linha.split("=", 1)[0].strip() in chaves)
    ]
    resultado.extend(f"{chave} = {valor}" for chave, valor in parametros.items())

    try:
        destino.write_text("\n".join(resultado) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ErroConversao(f"Não foi possível gravar o perfil temporário: {exc}") from exc


def _executar_slicer(
    comando: list[str],
    cwd: str,
    timeout_s: float,
    cancelar: Callable[[], bool] | None,
    log: LogFn,
) -> tuple[int, list[str]]:
    """
    Executa o fatiador sem poder travar indefinidamente.

    Usa Popen + communicate em fatias curtas para poder checar cancelamento e
    prazo. Sem isso, um fatiador com janela modal ou malha inválida deixa a
    interface presa para sempre.
    """
    opcoes: dict[str, object] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "cwd": cwd,
    }
    if sys.platform == "win32":
        opcoes["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        processo = subprocess.Popen(comando, **opcoes)
    except FileNotFoundError as exc:
        raise ErroConversao(f"Não foi possível executar o PrusaSlicer: {comando[0]}") from exc
    except OSError as exc:
        raise ErroConversao(f"Não foi possível iniciar o PrusaSlicer: {exc}") from exc

    prazo = time.monotonic() + timeout_s
    try:
        while True:
            if cancelar is not None and cancelar():
                raise ErroConversao("Conversão cancelada pelo usuário.")
            restante = prazo - time.monotonic()
            if restante <= 0:
                raise ErroConversao(
                    f"O PrusaSlicer demorou mais de {timeout_s:g} segundos e foi "
                    "encerrado. Isso costuma acontecer quando o modelo está corrompido "
                    "ou quando o fatiador abre uma janela de erro."
                )
            try:
                saida, _ = processo.communicate(timeout=min(0.25, restante))
                break
            except subprocess.TimeoutExpired:
                continue
    finally:
        if processo.poll() is None:
            processo.kill()
        try:
            processo.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    linhas = (saida or "").splitlines()
    if len(linhas) > MAX_LINHAS_LOG:
        log("warn", f"Saída do fatiador limitada às últimas {MAX_LINHAS_LOG} linhas.")
        linhas = linhas[-MAX_LINHAS_LOG:]
    return processo.returncode or 0, linhas


def converter_3mf_para_k7(
    arquivo_3mf: str | Path,
    medida_maxima_mm: float,
    *,
    prusa_cli: str | Path | None = None,
    config_ini: str | Path | None = None,
    arquivo_gcode: str | Path | None = None,
    log: LogFn = _sem_log,
    timeout_s: float = TIMEOUT_FATIAMENTO_S,
    cancelar: Callable[[], bool] | None = None,
    tipo_suporte: str | None = None,
    quantidade: int = 1,
    suporte_fino: bool = False,
    suporte_fino_mm: float = SUPORTE_FINO_PADRAO_MM,
) -> ResultadoConversao:
    """Redimensiona, centraliza e fatia o arquivo para a Easythreed K7."""
    caminho = Path(arquivo_3mf).expanduser()
    if not caminho.is_file():
        raise ErroConversao(f"Arquivo não encontrado: {caminho}")

    medida = validar_medida(medida_maxima_mm, log)

    cli = Path(prusa_cli).expanduser() if prusa_cli else localizar_prusa_cli()
    if cli is None:
        raise ErroConversao(
            "PrusaSlicer não encontrado. Informe o caminho do "
            "prusa-slicer-console.exe na aba Configurações."
        )
    if not cli.is_file():
        raise ErroConversao(f"Executável do PrusaSlicer não encontrado: {cli}")

    config = Path(config_ini).expanduser() if config_ini else CONFIG_PADRAO
    if not config.is_file():
        raise ErroConversao(f"Arquivo de configuração não encontrado: {config}")

    destino = (
        Path(arquivo_gcode).expanduser()
        if arquivo_gcode
        else caminho.with_name(f"{caminho.stem}_K7.gcode")
    )
    if destino.suffix.lower() != ".gcode":
        destino = destino.with_suffix(".gcode")
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ErroConversao(f"Não foi possível criar a pasta de saída: {exc}") from exc

    if cancelar is not None and cancelar():
        raise ErroConversao("Conversão cancelada pelo usuário.")

    malha, analise = _carregar_e_preparar(caminho, medida, log, quantidade)

    if cancelar is not None and cancelar():
        raise ErroConversao("Conversão cancelada pelo usuário.")

    config_efetiva = config
    ini_suporte: Path | None = None
    if tipo_suporte is not None or suporte_fino:
        nome_tipo = tipo_suporte or SUPORTE_PADRAO
        modelo = TIPOS_SUPORTE.get(nome_tipo)
        if modelo is None:
            raise ErroConversao(f"Tipo de suporte desconhecido: {nome_tipo}")
        parametros = dict(modelo)
        espessura_fina: float | None = None
        if suporte_fino and parametros.get("support_material") == "1":
            espessura_fina = validar_suporte_fino_mm(suporte_fino_mm, log)
            parametros.update(parametros_suporte_fino(espessura_fina))
        descritor_ini, nome_ini = tempfile.mkstemp(
            prefix="k7_suporte_", suffix=".ini", dir=str(config.parent)
        )
        os.close(descritor_ini)
        ini_suporte = Path(nome_ini)
        try:
            _gravar_ini_com_suporte(config, parametros, ini_suporte)
        except Exception:
            ini_suporte.unlink(missing_ok=True)
            raise
        config_efetiva = ini_suporte
        detalhe = (
            f" (mais fino: {espessura_fina:g} mm)"
            if espessura_fina is not None
            else ""
        )
        log("info", f"Tipo de suporte: {nome_tipo}{detalhe}")

    descritor, stl_temporario = tempfile.mkstemp(
        prefix="k7_", suffix=".stl", dir=str(destino.parent)
    )
    os.close(descritor)

    try:
        log("info", "Exportando malha ajustada para um STL temporário...")
        malha.export(stl_temporario)

        comando = [
            str(cli),
            "--export-gcode",
            "--load",
            str(config_efetiva),
            "--output",
            str(destino),
            stl_temporario,
        ]
        log("debug", "Comando: " + " ".join(f'"{argumento}"' for argumento in comando))
        log("info", "Fatiando com o PrusaSlicer (pode levar alguns segundos)...")

        codigo, linhas = _executar_slicer(
            comando, str(destino.parent), timeout_s, cancelar, log
        )
        for linha in linhas:
            if linha.strip():
                log("debug", linha.strip())

        if codigo != 0:
            log("error", f"O PrusaSlicer encerrou com código {codigo}.")
            for linha in linhas[-20:]:
                log("error", linha)
            raise ErroConversao("O fatiamento falhou. Confira o log para mais detalhes.")

        if not destino.is_file() or destino.stat().st_size == 0:
            raise ErroConversao(
                f"O PrusaSlicer não gerou um G-code válido em '{destino}'."
            )

        tamanho_kb = destino.stat().st_size / 1024
        log("success", f"Pronto! {destino.name} ({tamanho_kb:.1f} KB)")
    finally:
        for temporario in (stl_temporario, ini_suporte):
            if temporario is None:
                continue
            try:
                if os.path.exists(temporario):
                    os.remove(temporario)
            except OSError:
                pass

    return ResultadoConversao(
        arquivo_entrada=caminho,
        arquivo_gcode=destino,
        analise=analise,
    )


def converter_stl_para_k7(
    arquivo_stl: str | Path,
    medida_maxima_mm: float,
    **opcoes,
) -> ResultadoConversao:
    """Alias semântico para converter arquivos STL (mesmo pipeline do 3MF)."""
    return converter_3mf_para_k7(arquivo_stl, medida_maxima_mm, **opcoes)


def main() -> None:
    """Modo linha de comando (mantém o comportamento original do script)."""
    arquivo = input("Digite o nome/caminho do arquivo .3mf ou .stl: ").strip().strip('"')
    tamanho = float(input("Qual o tamanho máximo desejado em mm (ex: 60)? "))

    def log(_nivel: str, mensagem: str) -> None:
        print(mensagem)

    try:
        converter_3mf_para_k7(arquivo, tamanho, log=log)
    except ErroConversao as exc:
        print(f"ERRO: {exc}")


if __name__ == "__main__":
    main()
