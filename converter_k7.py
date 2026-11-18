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

LogFn = Callable[[str, str], None]


def _sem_log(_nivel: str, _mensagem: str) -> None:
    """Callback de log padrão (silencioso)."""
    return None


class ErroConversao(RuntimeError):
    """Erro de conversão já formatado para ser exibido ao usuário."""


@dataclass(frozen=True)
class AnaliseMalha:
    """Medidas da peça antes e depois do ajuste."""

    dimensoes_originais: tuple[float, float, float]
    dimensoes_finais: tuple[float, float, float]
    fator_escala: float
    medida_aplicada: float
    partes: int


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


def _formatar_dimensoes(dimensoes: tuple[float, float, float]) -> str:
    return " x ".join(f"{valor:.2f}" for valor in dimensoes)


def _carregar_e_preparar(
    caminho: Path, medida: float, log: LogFn
) -> tuple[trimesh.Trimesh, AnaliseMalha]:
    """Carrega a malha, escala proporcionalmente e centraliza na mesa da K7."""
    log("info", f"Carregando {caminho.name}...")

    try:
        cena = trimesh.load(str(caminho))
    except Exception as exc:  # trimesh levanta vários tipos de exceção
        raise ErroConversao(f"Não foi possível ler '{caminho.name}': {exc}") from exc

    if isinstance(cena, trimesh.Scene):
        if len(cena.geometry) == 0:
            raise ErroConversao("O arquivo não contém nenhuma geometria.")
        partes = len(cena.geometry)
        malha = cena.dump(concatenate=True)
    elif isinstance(cena, trimesh.Trimesh):
        partes = 1
        malha = cena
    else:
        raise ErroConversao("Formato de malha não suportado pelo trimesh.")

    if malha is None or malha.is_empty:
        raise ErroConversao("A malha carregada está vazia.")

    dimensoes_originais = tuple(float(v) for v in malha.extents)
    maior_lado = max(dimensoes_originais)
    if not math.isfinite(maior_lado) or maior_lado <= 0:
        raise ErroConversao("A malha não possui dimensões válidas.")

    fator_escala = medida / maior_lado
    malha.apply_scale(fator_escala)

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
    )

    log("info", f"Dimensões originais: {_formatar_dimensoes(dimensoes_originais)} mm ({partes} parte(s))")
    log("info", f"Escala {fator_escala:.4f}x -> {_formatar_dimensoes(dimensoes_finais)} mm")
    log("info", "Posicionamento: centro em (50, 50) mm e base encostada em Z = 0 mm")

    return malha, analise


def analisar_arquivo(
    arquivo: str | Path, medida_maxima_mm: float, log: LogFn = _sem_log
) -> AnaliseMalha:
    """Só lê e mede a peça (usado pela prévia da interface)."""
    caminho = Path(arquivo).expanduser()
    if not caminho.is_file():
        raise ErroConversao(f"Arquivo não encontrado: {caminho}")

    medida = validar_medida(medida_maxima_mm, log)
    return _carregar_e_preparar(caminho, medida, log)[1]


def converter_3mf_para_k7(
    arquivo_3mf: str | Path,
    medida_maxima_mm: float,
    *,
    prusa_cli: str | Path | None = None,
    config_ini: str | Path | None = None,
    arquivo_gcode: str | Path | None = None,
    log: LogFn = _sem_log,
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

    malha, analise = _carregar_e_preparar(caminho, medida, log)

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
            str(config),
            "--output",
            str(destino),
            stl_temporario,
        ]
        log("debug", "Comando: " + " ".join(f'"{argumento}"' for argumento in comando))
        log("info", "Fatiando com o PrusaSlicer (pode levar alguns segundos)...")

        try:
            processo = subprocess.run(
                comando,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(destino.parent),
            )
        except FileNotFoundError as exc:
            raise ErroConversao(f"Não foi possível executar o PrusaSlicer: {cli}") from exc
        except subprocess.CalledProcessError as exc:
            detalhes = (exc.stderr or exc.stdout or "").strip()
            log("error", f"O PrusaSlicer encerrou com código {exc.returncode}.")
            for linha in detalhes.splitlines()[-20:]:
                log("error", linha)
            raise ErroConversao("O fatiamento falhou. Confira o log para mais detalhes.") from exc

        for linha in (processo.stdout or "").splitlines():
            if linha.strip():
                log("debug", linha.strip())

        if not destino.is_file() or destino.stat().st_size == 0:
            raise ErroConversao(
                f"O PrusaSlicer não gerou um G-code válido em '{destino}'."
            )

        tamanho_kb = destino.stat().st_size / 1024
        log("success", f"Pronto! {destino.name} ({tamanho_kb:.1f} KB)")
    finally:
        try:
            if os.path.exists(stl_temporario):
                os.remove(stl_temporario)
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
