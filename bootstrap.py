"""
Verificações executadas antes de abrir a interface.

Tudo o que é Python (trimesh, numpy, lxml, ttkbootstrap) já vem embutido no
.executável. A única dependência externa real é o PrusaSlicer, que é baixado
e instalado em modo silencioso quando não é encontrado.

Se algo falhar, a função verificar_e_instalar devolve uma lista de avisos com
o nome do item, o motivo e o link oficial de download.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from converter_k7 import CONFIG_EMBUTIDO, CONFIG_PADRAO, localizar_prusa_cli

API_ULTIMO_RELEASE = "https://api.github.com/repos/prusa3d/PrusaSlicer/releases/latest"
PRUSA_PAGINA = "https://github.com/prusa3d/PrusaSlicer/releases/latest"
PRUSA_SETUP_FALLBACK = (
    "https://github.com/prusa3d/PrusaSlicer/releases/download/"
    "version_2.9.7/PrusaSlicer-2.9.7-setup.exe"
)
PRUSA_SHA_FALLBACK = "7862babe08627be87120356dc9f443c094652c1f151a78ff234b0e461c46805e"

VC_DOWNLOAD = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
VC_PAGINA = "https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist"

DOWNLOAD_MAX_MB = 400
DOWNLOAD_PRAZO_S = 600
INSTALACAO_PRAZO_S = 600

# O perfil da K7 é copiado do arquivo embutido no .exe. Caso o arquivo embutido
# esteja ausente, ele é recriado com estes limites (que são os mesmos do
# k7_config.ini). A URL abaixo é o local oficial para obter um perfil alternativo.
CONFIG_PAGINA = "https://www.prusaslicer.org/"
CONFIG_PADRAO_TEXTO = """bed_shape = 0x0,100x0,100x100,0x100
max_print_height = 100
nozzle_diameter = 0.4
filament_diameter = 1.75
temperature = 195
first_layer_temperature = 200
bed_temperature = 0
first_layer_bed_temperature = 0
layer_height = 0.2
fill_density = 15%
perimeter_speed = 30
infill_speed = 35
travel_speed = 60
support_material = 1
support_material_auto = 1
start_gcode = G28 ; home all axes\\nG1 Z5 F5000 ; lift nozzle\\nM109 S[first_layer_temperature] ; wait for nozzle temp\\nG92 E0 ; zero extruder
end_gcode = M104 S0 ; turn off temperature\\nG28 X0 ; home X axis\\nM84 ; disable motors
"""

LogFn = Callable[[str], None]
ProgressoFn = Callable[[int], None]
TickFn = Callable[[], None]


@dataclass(frozen=True)
class AvisoDependencia:
    nome: str
    motivo: str
    url: str


def _usuario_http() -> urllib.request.Request:
    return urllib.request.Request(
        API_ULTIMO_RELEASE, headers={"User-Agent": "ConversorK7/1.0"}
    )


def _obter_instalador_prusa() -> tuple[str, str]:
    """Descobre o setup.exe mais recente no GitHub; usa um fallback offline."""
    try:
        with urllib.request.urlopen(_usuario_http(), timeout=10) as resposta:
            dados = json.load(resposta)
        for asset in dados.get("assets", []):
            if asset.get("name", "").lower().endswith("-setup.exe"):
                digest = str(asset.get("digest") or "").split(":")[-1]
                return asset["browser_download_url"], digest
    except Exception:
        pass
    return PRUSA_SETUP_FALLBACK, PRUSA_SHA_FALLBACK


def _baixar(
    url: str,
    destino: Path,
    log: LogFn,
    progresso: ProgressoFn,
    tick: TickFn | None = None,
) -> str:
    """Baixa com progresso, prazo e limite de tamanho (devolve o SHA-256)."""
    requisicao = urllib.request.Request(url, headers={"User-Agent": "ConversorK7/1.0"})
    sha = hashlib.sha256()
    inicio = time.monotonic()
    limite = DOWNLOAD_MAX_MB * 1024 * 1024
    try:
        with urllib.request.urlopen(requisicao, timeout=60) as resposta, destino.open(
            "wb"
        ) as arquivo:
            total = int(resposta.headers.get("Content-Length") or 0)
            lido = 0
            while True:
                bloco = resposta.read(256 * 1024)
                if not bloco:
                    break
                arquivo.write(bloco)
                sha.update(bloco)
                lido += len(bloco)
                if lido > limite:
                    raise OSError(
                        f"download acima de {DOWNLOAD_MAX_MB} MB foi interrompido"
                    )
                if time.monotonic() - inicio > DOWNLOAD_PRAZO_S:
                    raise TimeoutError("download excedeu o tempo limite")
                if total:
                    progresso(min(96, int(lido / total * 100)))
                if tick:
                    tick()
    except Exception:
        destino.unlink(missing_ok=True)
        raise
    return sha.hexdigest()


def _instalar_silencioso(
    instalador: Path, argumentos: list[str], tick: TickFn | None = None
) -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "instalação automática disponível apenas no Windows"
    try:
        processo = subprocess.Popen(
            [str(instalador), *argumentos],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        return False, str(exc)

    inicio = time.monotonic()
    try:
        while processo.poll() is None:
            if time.monotonic() - inicio > INSTALACAO_PRAZO_S:
                processo.kill()
                processo.wait()
                return False, "tempo limite atingido ao instalar"
            if tick:
                tick()
            time.sleep(0.2)
    except Exception as exc:
        if processo.poll() is None:
            processo.kill()
        return False, str(exc)
    return processo.returncode == 0, f"código de saída {processo.returncode}"


def _verificar_vc(
    log: LogFn, progresso: ProgressoFn, tick: TickFn | None = None
) -> AvisoDependencia | None:
    """Visual C++ é necessário para alguns módulos nativos do Python."""
    if sys.platform != "win32":
        return None

    raiz = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    locais = [raiz]
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        locais.append(Path(sys._MEIPASS))
    if any((pasta / "vcruntime140.dll").is_file() for pasta in locais):
        return None

    try:
        log("Visual C++ Redistributable não encontrado — baixando...")
        progresso(97)
        destino = Path(tempfile.gettempdir()) / "vc_redist_x64_k7.exe"
        _baixar(VC_DOWNLOAD, destino, log, progresso, tick)
        ok, detalhe = _instalar_silencioso(
            destino, ["/install", "/quiet", "/norestart"], tick
        )
        destino.unlink(missing_ok=True)
        if ok:
            log("Visual C++ Redistributable instalado.")
            return None
        return AvisoDependencia(
            "Microsoft Visual C++ Redistributable x64",
            f"Não foi possível instalar automaticamente ({detalhe}).",
            VC_PAGINA,
        )
    except Exception as exc:
        return AvisoDependencia(
            "Microsoft Visual C++ Redistributable x64",
            f"Falha no download/instalação: {exc}",
            VC_PAGINA,
        )


def garantir_config_k7(log: LogFn) -> AvisoDependencia | None:
    """
    Garante que o perfil da K7 exista num caminho estável.

    No executável, o .ini vem embutido numa pasta temporária (_MEIxxxx) que é
    apagada a cada execução. Por isso ele é copiado para %LOCALAPPDATA%.
    Se o arquivo embutido faltar, o perfil é recriado com os limites padrão.
    """
    destino = CONFIG_PADRAO
    if destino.is_file() and destino.stat().st_size > 0:
        log(f"Perfil K7 encontrado: {destino}")
        return None

    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        if CONFIG_EMBUTIDO.is_file() and CONFIG_EMBUTIDO.stat().st_size > 0:
            destino.write_bytes(CONFIG_EMBUTIDO.read_bytes())
            log(f"Perfil K7 copiado para: {destino}")
            return None
    except OSError:
        pass

    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(CONFIG_PADRAO_TEXTO, encoding="utf-8")
        log(f"Perfil K7 recriado com os limites padrão: {destino}")
        return None
    except Exception as exc:
        return AvisoDependencia(
            "Perfil K7 (k7_config.ini)",
            f"Não foi possível criar o arquivo automaticamente ({exc}). "
            "Use o botão 'Baixar perfil' na aba Configurações.",
            CONFIG_PAGINA,
        )


def verificar_e_instalar(
    log: LogFn, progresso: ProgressoFn, tick: TickFn | None = None
) -> list[AvisoDependencia]:
    """
    Garante que o PrusaSlicer exista antes de abrir a janela principal.

    Devolve a lista de dependências que não puderam ser instaladas.
    """
    avisos: list[AvisoDependencia] = []

    aviso_config = garantir_config_k7(log)
    if aviso_config:
        avisos.append(aviso_config)

    encontrado = localizar_prusa_cli()
    if encontrado:
        log(f"PrusaSlicer encontrado: {encontrado}")
        progresso(100)
    else:
        try:
            progresso(3)
            log("PrusaSlicer não encontrado.Consultando o instalador oficial...")
            url, sha_esperado = _obter_instalador_prusa()

            log("Baixando o PrusaSlicer (aprox. 100 MB)...")
            destino = Path(tempfile.gettempdir()) / "PrusaSlicer_setup_k7.exe"
            sha_obtido = _baixar(url, destino, log, progresso, tick)

            if sha_esperado and sha_obtido.lower() != sha_esperado.lower():
                raise ValueError("o SHA-256 do arquivo baixado não confere")

            log("Instalando o PrusaSlicer em modo silencioso...")
            progresso(98)
            ok, detalhe = _instalar_silencioso(
                destino,
                ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"],
                tick,
            )
            destino.unlink(missing_ok=True)

            if ok and localizar_prusa_cli():
                log("PrusaSlicer instalado com sucesso.")
            else:
                avisos.append(
                    AvisoDependencia(
                        "PrusaSlicer",
                        f"A instalação automática não concluiu ({detalhe}).",
                        PRUSA_PAGINA,
                    )
                )
        except Exception as exc:
            avisos.append(
                AvisoDependencia(
                    "PrusaSlicer",
                    f"Não foi possível baixar/instalar automaticamente: {exc}",
                    PRUSA_PAGINA,
                )
            )

    aviso_vc = _verificar_vc(log, progresso, tick)
    if aviso_vc:
        avisos.append(aviso_vc)

    log(
        "Dependências Python (trimesh, numpy, lxml e ttkbootstrap) já estão "
        "embutidas no executável."
    )
    progresso(100)
    return avisos
