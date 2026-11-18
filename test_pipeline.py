r"""
Testes locais do pipeline e da interface.

Uso (sempre dentro do venv):
    .venv\Scripts\python.exe test_pipeline.py           # testes do núcleo
    .venv\Scripts\python.exe test_pipeline.py --gui     # + smoke test da GUI

Como o PrusaSlicer não é necessário para os testes, subprocess.run é
substituído por uma função que confere o comando montado e escreve um
G-code falso.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import trimesh

import converter_k7 as conv


def criar_3mf(caminho: Path, caixas: int = 1) -> None:
    """Gera um 3MF de teste com uma ou mais caixas."""
    cena = trimesh.Scene()
    for indice in range(caixas):
        caixa = trimesh.creation.box(extents=[40, 30, 20])
        caixa.apply_translation([123 + indice * 50, 77, 5])  # base em z = -5
        cena.add_geometry(caixa, node_name=f"parte_{indice}")

    dados = trimesh.exchange.threemf.export_3MF(cena)
    caminho.write_bytes(dados.encode() if isinstance(dados, str) else dados)


def verificar(malha: trimesh.Trimesh, medida_esperada: float) -> None:
    """Confere escala e posicionamento da malha enviada ao fatiador."""
    minimo, maximo = malha.bounds
    centro_x = (minimo[0] + maximo[0]) / 2
    centro_y = (minimo[1] + maximo[1]) / 2

    assert abs(max(malha.extents) - medida_esperada) < 1e-3, malha.extents
    assert abs(centro_x - 50) < 1e-3, centro_x
    assert abs(centro_y - 50) < 1e-3, centro_y
    assert abs(minimo[2]) < 1e-3, minimo[2]


def testar_conversao_completa(diretorio: Path) -> None:
    modelo = diretorio / "peca.3mf"
    criar_3mf(modelo)
    cli_falso = diretorio / "prusa-slicer-console.exe"
    cli_falso.write_bytes(b"falso")

    comandos: list[list[str]] = []
    mensagens: list[tuple[str, str]] = []

    def falso_slicer(comando: list[str], **_kwargs):
        comandos.append(comando)
        assert comando[0] == str(cli_falso)
        assert comando[1] == "--export-gcode"
        assert comando[2] == "--load"
        assert Path(comando[3]).is_file()
        assert comando[4] == "--output"

        destino = Path(comando[5])
        stl = Path(comando[6])
        assert stl.is_file(), "o STL temporário deve existir durante o fatiamento"

        malha_fatiada = trimesh.load(str(stl))
        verificar(malha_fatiada, 60.0)

        destino.write_text("; gcode falso\nG28\n", encoding="utf-8")
        return subprocess.CompletedProcess(comando, 0, "slicer falso: ok", "")

    with patch.object(conv.subprocess, "run", side_effect=falso_slicer):
        resultado = conv.converter_3mf_para_k7(
            modelo,
            60,
            prusa_cli=cli_falso,
            config_ini=conv.CONFIG_PADRAO,
            log=lambda nivel, mensagem: mensagens.append((nivel, mensagem)),
        )

    assert resultado.arquivo_gcode == diretorio / "peca_K7.gcode"
    assert resultado.arquivo_gcode.read_text(encoding="utf-8").startswith("; gcode falso")
    assert abs(max(resultado.analise.dimensoes_originais) - 40) < 1e-3
    assert abs(max(resultado.analise.dimensoes_finais) - 60) < 1e-3
    assert abs(resultado.analise.fator_escala - 1.5) < 1e-6
    assert not list(diretorio.glob("k7_*.stl")), "STL temporário não foi removido"
    assert any(nivel == "success" for nivel, _ in mensagens)
    print("  [ok] conversao completa (3MF -> STL ajustado -> G-code)")


def testar_trava_seguranca(diretorio: Path) -> None:
    modelo = diretorio / "grande.3mf"
    criar_3mf(modelo)
    cli_falso = diretorio / "cli.exe"
    cli_falso.write_bytes(b"falso")

    def falso_slicer(comando: list[str], **_kwargs):
        stl = Path(comando[-1])
        verificar(trimesh.load(str(stl)), 95.0)
        Path(comando[comando.index("--output") + 1]).write_text("; ok\n", encoding="utf-8")
        return subprocess.CompletedProcess(comando, 0, "", "")

    mensagens: list[str] = []
    with patch.object(conv.subprocess, "run", side_effect=falso_slicer):
        resultado = conv.converter_3mf_para_k7(
            modelo,
            200,
            prusa_cli=cli_falso,
            config_ini=conv.CONFIG_PADRAO,
            arquivo_gcode=diretorio / "saida_custom.gcode",
            log=lambda _nivel, mensagem: mensagens.append(mensagem),
        )

    assert resultado.arquivo_gcode.name == "saida_custom.gcode"
    assert resultado.analise.medida_aplicada == 95.0
    assert any("limite" in mensagem.lower() for mensagem in mensagens)
    print("  [ok] trava de seguranca de 95 mm e nome de saida personalizado")


def testar_conversao_stl(diretorio: Path) -> None:
    """ confere que um .stl comum passa pelo mesmo pipeline do .3mf """
    modelo = diretorio / "modelo.stl"
    caixa = trimesh.creation.box(extents=[20, 10, 30])
    caixa.export(str(modelo))

    cli_falso = diretorio / "cli_stl.exe"
    cli_falso.write_bytes(b"falso")

    def falso_slicer(comando: list[str], **_kwargs):
        verificar(trimesh.load(str(Path(comando[-1]))), 70.0)
        Path(comando[comando.index("--output") + 1]).write_text("; ok\n", encoding="utf-8")
        return subprocess.CompletedProcess(comando, 0, "", "")

    with patch.object(conv.subprocess, "run", side_effect=falso_slicer):
        resultado = conv.converter_stl_para_k7(
            modelo, 70, prusa_cli=cli_falso, config_ini=conv.CONFIG_PADRAO
        )

    assert resultado.arquivo_gcode == diretorio / "modelo_K7.gcode"
    assert abs(max(resultado.analise.dimensoes_finais) - 70) < 1e-3
    print("  [ok] conversao de .stl para .gcode (alias converter_stl_para_k7)")


def testar_multiplas_partes_e_erros(diretorio: Path) -> None:
    modelo = diretorio / "multiplas_partes.3mf"
    criar_3mf(modelo, caixas=2)

    analise = conv.analisar_arquivo(modelo, 50)
    assert analise.partes == 2, analise.partes
    assert abs(max(analise.dimensoes_finais) - 50) < 1e-3
    print("  [ok] 3MF com multiplas partes")

    try:
        conv.analisar_arquivo(diretorio / "nao_existe.3mf", 50)
    except conv.ErroConversao:
        print("  [ok] erro amigavel para arquivo inexistente")
    else:
        raise AssertionError("esperava ErroConversao")

    try:
        conv.validar_medida(0)
    except conv.ErroConversao:
        print("  [ok] medida invalida rejeitada")
    else:
        raise AssertionError("esperava ErroConversao para medida zero")


def testar_preflight() -> None:
    """ confere a logica de verificacao sem baixar nada de verdade """
    import bootstrap

    with patch.object(bootstrap, "localizar_prusa_cli", return_value=Path("fake.exe")), patch.object(
        bootstrap, "_verificar_vc", return_value=None
    ), patch.object(bootstrap, "garantir_config_k7", return_value=None):
        mensagens: list[str] = []
        avisos = bootstrap.verificar_e_instalar(mensagens.append, lambda _valor: None)
        assert avisos == []
        assert any("encontrado" in m for m in mensagens)
    print("  [ok] preflight detecta PrusaSlicer instalado")

    with patch.object(bootstrap, "localizar_prusa_cli", return_value=None), patch.object(
        bootstrap, "_verificar_vc", return_value=None
    ), patch.object(bootstrap, "garantir_config_k7", return_value=None), patch.object(
        bootstrap,
        "_baixar",
        side_effect=RuntimeError("sem rede"),
    ):
        avisos = bootstrap.verificar_e_instalar(lambda _m: None, lambda _v: None)
        assert len(avisos) == 1
        assert avisos[0].nome == "PrusaSlicer"
        assert avisos[0].url.startswith("https://")
    print("  [ok] preflight gera aviso com link quando nao consegue instalar")


def testar_perfil_ini(diretorio: Path) -> None:
    """O perfil e copiado para um caminho estavel e recriado se faltar."""
    import bootstrap

    destino = diretorio / "k7_config.ini"
    origem = diretorio / "origem.ini"
    origem.write_text("bed_shape = 0x0,100x0", encoding="utf-8")

    with patch.object(bootstrap, "CONFIG_PADRAO", destino), patch.object(
        bootstrap, "CONFIG_EMBUTIDO", origem
    ):
        assert bootstrap.garantir_config_k7(lambda _m: None) is None
        assert "bed_shape" in destino.read_text(encoding="utf-8")

        destino.unlink()
        with patch.object(bootstrap, "CONFIG_EMBUTIDO", diretorio / "nao_existe.ini"):
            assert bootstrap.garantir_config_k7(lambda _m: None) is None
            assert destino.is_file()
            assert "support_material_auto" in destino.read_text(encoding="utf-8")
    print("  [ok] perfil .ini criado em caminho estavel e recriado se faltar")


def testar_gui() -> None:
    from app import AppK7

    janela = AppK7()
    janela.withdraw()
    janela.update_idletasks()
    janela.update()
    assert janela.winfo_exists()
    janela.destroy()
    print("  [ok] interface ttkbootstrap constroi e fecha sem erros")


def main() -> None:
    print("Testando nucleo de conversao:")
    with tempfile.TemporaryDirectory(prefix="k7_teste_") as temporario:
        diretorio = Path(temporario)
        testar_conversao_completa(diretorio)
        testar_trava_seguranca(diretorio)
        testar_conversao_stl(diretorio)
        testar_multiplas_partes_e_erros(diretorio)
        testar_preflight()
        testar_perfil_ini(diretorio)

    if "--gui" in sys.argv:
        print("Testando interface:")
        testar_gui()

    print("Todos os testes passaram.")


if __name__ == "__main__":
    main()
