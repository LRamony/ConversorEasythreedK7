r"""
Testes locais do pipeline e da interface.

Uso (sempre dentro do venv):
    .venv\Scripts\python.exe test_pipeline.py           # testes do núcleo
    .venv\Scripts\python.exe test_pipeline.py --gui     # + smoke test da GUI

Como o PrusaSlicer não é necessário para os testes, a execução do fatiador
(_executar_slicer) é substituída por uma função que confere o comando montado
e escreve um G-code falso.
"""

from __future__ import annotations

import json
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

    def falso_slicer(comando: list[str], *_args, **_kwargs):
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
        return 0, ["slicer falso: ok"]

    with patch.object(conv, "_executar_slicer", side_effect=falso_slicer):
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

    def falso_slicer(comando: list[str], *_args, **_kwargs):
        stl = Path(comando[-1])
        verificar(trimesh.load(str(stl)), 95.0)
        Path(comando[comando.index("--output") + 1]).write_text("; ok\n", encoding="utf-8")
        return 0, []

    mensagens: list[str] = []
    with patch.object(conv, "_executar_slicer", side_effect=falso_slicer):
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

    def falso_slicer(comando: list[str], *_args, **_kwargs):
        verificar(trimesh.load(str(Path(comando[-1]))), 70.0)
        Path(comando[comando.index("--output") + 1]).write_text("; ok\n", encoding="utf-8")
        return 0, []

    with patch.object(conv, "_executar_slicer", side_effect=falso_slicer):
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


def testar_protecoes_travamento(diretorio: Path) -> None:
    """Confere timeout, cancelamento e limites que evitam travar o PC."""
    modelo = diretorio / "protecoes.3mf"
    criar_3mf(modelo)
    cli_falso = diretorio / "cli_protecoes.exe"
    cli_falso.write_bytes(b"falso")

    # Cancelamento antes de qualquer trabalho pesado.
    try:
        conv.converter_3mf_para_k7(
            modelo,
            50,
            prusa_cli=cli_falso,
            config_ini=conv.CONFIG_PADRAO,
            cancelar=lambda: True,
        )
    except conv.ErroConversao as exc:
        assert "cancelada" in str(exc).lower(), exc
    else:
        raise AssertionError("esperava ErroConversao ao cancelar")
    print("  [ok] cancelamento interrompe a conversao")

    # Arquivo acima do limite é recusado antes de carregar a malha.
    with patch.object(conv, "TAMANHO_MAXIMO_ARQUIVO_MB", 0.0001):
        try:
            conv.analisar_arquivo(modelo, 50)
        except conv.ErroConversao as exc:
            assert "grande" in str(exc).lower(), exc
        else:
            raise AssertionError("esperava ErroConversao para arquivo grande")
    print("  [ok] arquivo muito grande e recusado")

    # Malha com triângulos acima do limite também é recusada.
    with patch.object(conv, "MAX_FACES", 1):
        try:
            conv.analisar_arquivo(modelo, 50)
        except conv.ErroConversao as exc:
            assert "tri" in str(exc).lower(), exc
        else:
            raise AssertionError("esperava ErroConversao para malha pesada")
    print("  [ok] malha com muitos triangulos e recusada")

    # Fatiador que nunca responde é encerrado no prazo.
    class ProcessoPreso:
        returncode: int | None = None

        def poll(self):
            return None

        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired(["fake"], timeout)

        def kill(self):
            self.returncode = -9

        def wait(self, timeout=None):
            self.returncode = -9
            return self.returncode

    with patch.object(conv.subprocess, "Popen", return_value=ProcessoPreso()):
        try:
            conv._executar_slicer(
                ["fake"], str(diretorio), 0.05, None, lambda _n, _m: None
            )
        except conv.ErroConversao as exc:
            assert "demorou" in str(exc).lower(), exc
        else:
            raise AssertionError("esperava ErroConversao por timeout")
    print("  [ok] fatiador preso e encerrado por timeout")


def testar_tipo_suporte(diretorio: Path) -> None:
    """O estilo de suporte é aplicado num .ini temporário e depois removido."""
    perfil = diretorio / "perfil_suporte.ini"
    perfil.write_text(
        "support_material = 1\nlayer_height = 0.2\n", encoding="utf-8"
    )
    destino = diretorio / "temporario.ini"
    conv._gravar_ini_com_suporte(
        perfil, conv.TIPOS_SUPORTE["Árvore - Orgânico"], destino
    )
    texto = destino.read_text(encoding="utf-8")
    assert "support_material_style = organic" in texto
    assert "support_tree_angle = 40" in texto
    assert "support_tree_angle_slow = 25" in texto
    assert "layer_height = 0.2" in texto
    assert texto.count("support_material = ") == 1

    fino = dict(conv.TIPOS_SUPORTE["Árvore - Orgânico"])
    fino.update(conv.parametros_suporte_fino(1.05))
    destino_fino = diretorio / "temporario_fino.ini"
    conv._gravar_ini_com_suporte(perfil, fino, destino_fino)
    texto_fino = destino_fino.read_text(encoding="utf-8")
    assert "support_material_extrusion_width = 0.35" in texto_fino
    assert "support_material_interface_extrusion_width = 0.35" in texto_fino
    assert "support_material_with_sheath = 0" in texto_fino
    assert "support_tree_branch_diameter = 1.05" in texto_fino
    assert "support_tree_tip_diameter = 0.53" in texto_fino
    assert "support_tree_branch_diameter_angle = 0" in texto_fino
    assert "support_tree_branch_diameter_double_wall = 0" in texto_fino
    assert "support_tree_branch_distance = 2" in texto_fino
    assert "support_tree_top_rate = 5%" in texto_fino
    assert texto_fino.count("support_material_extrusion_width = ") == 1
    assert conv.validar_suporte_fino_mm(9) == conv.SUPORTE_FINO_MAX_MM
    assert conv.validar_suporte_fino_mm(-1) == conv.SUPORTE_FINO_MIN_MM
    grosso = conv.parametros_suporte_fino(2.5)
    assert grosso["support_tree_branch_diameter"] == "2.5"
    assert float(grosso["support_tree_tip_diameter"]) > 0.53
    assert grosso["support_material_extrusion_width"] == "0.5"
    extrusao_min = float(
        conv.parametros_suporte_fino(conv.SUPORTE_FINO_MIN_MM)[
            "support_material_extrusion_width"
        ]
    )
    assert extrusao_min > 0.2  # acima da altura de camada do perfil
    print("  [ok] perfil temporario afina a arvore e impede tronco grosso")

    modelo = diretorio / "suporte.3mf"
    criar_3mf(modelo)
    cli_falso = diretorio / "cli_suporte.exe"
    cli_falso.write_bytes(b"falso")
    vistos: list[str] = []

    def falso_slicer(comando: list[str], *_args, **_kwargs):
        vistos.append(Path(comando[3]).read_text(encoding="utf-8"))
        Path(comando[comando.index("--output") + 1]).write_text("; ok\n", encoding="utf-8")
        return 0, []

    with patch.object(conv, "_executar_slicer", side_effect=falso_slicer):
        conv.converter_3mf_para_k7(
            modelo,
            50,
            prusa_cli=cli_falso,
            config_ini=conv.CONFIG_PADRAO,
            tipo_suporte="Árvore - Orgânico",
            suporte_fino=True,
            suporte_fino_mm=1.05,
        )
    assert any("support_material_style = organic" in trecho for trecho in vistos)
    assert any("support_material_extrusion_width = 0.35" in trecho for trecho in vistos)
    assert any("support_tree_branch_diameter = 1.05" in trecho for trecho in vistos)
    assert any("support_tree_branch_diameter_angle = 0" in trecho for trecho in vistos)
    assert not list(diretorio.glob("k7_suporte_*.ini")), "ini temporário não removido"

    vistos.clear()
    with patch.object(conv, "_executar_slicer", side_effect=falso_slicer):
        conv.converter_3mf_para_k7(
            modelo,
            50,
            prusa_cli=cli_falso,
            config_ini=conv.CONFIG_PADRAO,
            tipo_suporte="Sem suporte",
            suporte_fino=True,
        )
    assert vistos and "support_material = 0" in vistos[-1]
    assert not any("support_material_extrusion_width" in trecho for trecho in vistos)
    assert not list(diretorio.glob("k7_suporte_*.ini")), "ini temporário não removido"
    print("  [ok] conversao ajusta o diametro e ignora a opcao sem suporte")


def testar_ajustes_impressao(diretorio: Path) -> None:
    """Preenchimento, velocidades e temperaturas têm limites e vão no .ini."""
    perfil = diretorio / "perfil_ajustes.ini"
    perfil.write_text(
        "fill_density = 12%\n"
        "perimeter_speed = 30\n"
        "temperature = 195\n"
        "bed_temperature = 0\n",
        encoding="utf-8",
    )
    lidos = conv.ler_parametros_impressao(perfil)
    assert lidos["fill_density"] == 12
    assert lidos["infill_speed"] == 35  # padrão quando a chave não existe
    assert conv.limitar_impressao("fill_density", 900) == 100
    assert conv.limitar_impressao("temperature", -10) == 180
    assert conv.limitar_impressao("travel_speed", 33.6) == 34

    valores = {
        "fill_density": 40,
        "perimeter_speed": 999,
        "infill_speed": 45,
        "travel_speed": 90,
        "temperature": 210,
        "first_layer_temperature": 215,
        "bed_temperature": 0,
        "first_layer_bed_temperature": 0,
    }
    parametros = conv.parametros_config_impressao(valores)
    assert parametros["fill_density"] == "40%"
    assert parametros["perimeter_speed"] == "60"  # limite máximo
    assert parametros["travel_speed"] == "90"
    assert parametros["temperature"] == "210"

    destino_ini = diretorio / "temporario_ajustes.ini"
    conv._gravar_ini_com_suporte(perfil, parametros, destino_ini)
    texto = destino_ini.read_text(encoding="utf-8")
    assert "fill_density = 40%" in texto
    assert "perimeter_speed = 60" in texto
    assert "temperature = 210" in texto
    assert texto.count("fill_density = ") == 1

    modelo = diretorio / "ajustes.3mf"
    criar_3mf(modelo)
    cli_falso = diretorio / "cli_ajustes.exe"
    cli_falso.write_bytes(b"falso")
    vistos: list[str] = []

    def falso_slicer(comando: list[str], *_args, **_kwargs):
        vistos.append(Path(comando[3]).read_text(encoding="utf-8"))
        Path(comando[comando.index("--output") + 1]).write_text("; ok\n", encoding="utf-8")
        return 0, []

    with patch.object(conv, "_executar_slicer", side_effect=falso_slicer):
        conv.converter_3mf_para_k7(
            modelo,
            50,
            prusa_cli=cli_falso,
            config_ini=perfil,
            tipo_suporte="Sem suporte",
            config_impressao=valores,
        )
    assert any("fill_density = 40%" in trecho for trecho in vistos)
    assert any("support_material = 0" in trecho for trecho in vistos)
    assert not list(diretorio.glob("k7_suporte_*.ini")), "ini temporário não removido"
    print("  [ok] ajustes rapidos limitados e aplicados no perfil temporario")


def testar_quantidade(diretorio: Path) -> None:
    """Várias cópias são organizadas em grade e reduzem a escala automaticamente."""
    modelo = diretorio / "quantidade.stl"
    trimesh.creation.box(extents=[40, 30, 20]).export(modelo)

    uma = conv.analisar_arquivo(modelo, 95)
    quatro = conv.analisar_arquivo(modelo, 95, quantidade=4)

    assert uma.quantidade == 1 and quatro.quantidade == 4
    assert quatro.colunas * quatro.linhas >= 4
    assert quatro.fator_escala < uma.fator_escala
    assert max(quatro.dimensoes_finais[:2]) <= conv.LIMITE_SEGURANCA_MM + 1e-6
    por_peca = max(
        valor * quatro.fator_escala for valor in quatro.dimensoes_originais
    )
    assert por_peca < conv.LIMITE_SEGURANCA_MM

    try:
        conv.validar_quantidade(0)
    except conv.ErroConversao:
        pass
    else:
        raise AssertionError("quantidade zero deveria ser rejeitada")
    assert conv.validar_quantidade(999) == conv.QUANTIDADE_MAXIMA
    print("  [ok] multiplas pecas cabem na mesa com escala automatica")


def testar_calculo() -> None:
    """A aba Calc lê tempo/filamento do G-code e aplica a margem."""
    import calculo

    with tempfile.TemporaryDirectory(prefix="k7_calc_") as temporario:
        gcode = Path(temporario) / "peca_K7.gcode"
        gcode.write_text(
            "; generated by PrusaSlicer 2.9.7\n"
            "; filament used [mm] = 1000.0\n"
            "; filament used [g] = 3.0\n"
            "; estimated printing time (normal mode) = 2h 30m 0s\n"
            "G28\n",
            encoding="utf-8",
        )
        dados = calculo.extrair_dados_gcode(gcode)
        assert abs(dados.horas - 2.5) < 1e-6, dados.horas
        assert abs(dados.filamento_g - 3.0) < 1e-6, dados.filamento_g

        orcamento = calculo.calcular_orcamento(dados, 30)
        assert abs(orcamento.custo_total - 45.188) < 0.01, orcamento.custo_total
        assert abs(orcamento.preco_final - 45.188 * 1.3) < 0.01
        assert abs(orcamento.lucro - (orcamento.preco_final - orcamento.custo_total)) < 1e-6
        assert len(orcamento.linhas()) == 6
    print("  [ok] Calc extrai dados do G-code e calcula margem/custos")

    # O PrusaSlicer grava o resumo no RODAPÉ, depois de milhares de linhas.
    with tempfile.TemporaryDirectory(prefix="k7_calc_") as temporario:
        gcode = Path(temporario) / "rodape_K7.gcode"
        linhas = ["; generated by PrusaSlicer 2.9.7"]
        linhas.extend(
            f"G1 X{i % 100} Y{i % 50} E0.1 F1200" for i in range(2000)
        )
        linhas.extend(
            [
                "; filament used [mm] = 2500.0",
                "; filament used [cm3] = 6.01",
                "; filament used [g] = 7.45",
                "; total filament used [g] = 7.45",
                "; estimated first layer printing time (normal mode) = 2m 10s",
                "; estimated printing time (normal mode) = 1d 2h 3m 4s",
                "; prusaslicer_config = begin",
                "layer_height = 0.2",
                "; prusaslicer_config = end",
            ]
        )
        gcode.write_text("\n".join(linhas) + "\n", encoding="utf-8")
        dados = calculo.extrair_dados_gcode(gcode)
        esperado = (86400 + 2 * 3600 + 3 * 60 + 4) / 3600
        assert abs(dados.horas - esperado) < 1e-6, dados.horas
        assert abs(dados.filamento_g - 7.45) < 1e-6, dados.filamento_g
    print("  [ok] Calc encontra o resumo no rodape do G-code do PrusaSlicer")

    # Formatos alternativos de tempo ainda devem funcionar.
    with tempfile.TemporaryDirectory(prefix="k7_calc_") as temporario:
        for texto, esperado in (
            (
                "; estimated printing time = 01:23:45\n"
                "; filament used [g] = 1.0\n",
                1 + 23 / 60 + 45 / 3600,
            ),
            (
                ";TIME:38846\n; filament used [g] = 1.0\n",
                38846 / 3600,
            ),
        ):
            gcode = Path(temporario) / "formato.gcode"
            gcode.write_text(texto, encoding="utf-8")
            dados = calculo.extrair_dados_gcode(gcode)
            assert abs(dados.horas - esperado) < 1e-6, (texto, dados.horas)
    print("  [ok] Calc aceita formatos HH:MM:SS e segundos")

    # Sem tempo no G-code, usa o padrão editável de 1,5 h.
    with tempfile.TemporaryDirectory(prefix="k7_calc_") as temporario:
        gcode = Path(temporario) / "sem_tempo.gcode"
        gcode.write_text(
            "; generated by PrusaSlicer 2.9.7\n"
            "; filament used [g] = 5.0\n"
            "G28\n",
            encoding="utf-8",
        )
        dados = calculo.extrair_dados_gcode(gcode, horas_padrao=1.5)
        assert abs(dados.horas - 1.5) < 1e-6, dados.horas
        assert dados.tempo_encontrado is False
        assert abs(dados.filamento_g - 5.0) < 1e-6
        try:
            calculo.extrair_dados_gcode(gcode)
        except calculo.ErroCalculo:
            pass
        else:
            raise AssertionError("sem fallback deveria avisar que o tempo falta")
    print("  [ok] Calc usa 1,5 h editavel quando o G-code nao tem tempo")

    # Quando o G-code não informa o peso, ele é estimado pela mm de filamento.
    with tempfile.TemporaryDirectory(prefix="k7_calc_") as temporario:
        gcode = Path(temporario) / "sem_peso.gcode"
        gcode.write_text(
            "; filament used [mm] = 1000.0\n"
            "; estimated printing time (normal mode) = 45m\n",
            encoding="utf-8",
        )
        dados = calculo.extrair_dados_gcode(gcode)
        assert abs(dados.horas - 0.75) < 1e-6, dados.horas
        assert dados.filamento_g > 0
    print("  [ok] Calc estima o peso quando o G-code so informa milimetros")


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
    import tkinter.ttk as ttk_widgets

    import app as app_modulo

    with tempfile.TemporaryDirectory(prefix="k7_prefs_gui_") as pasta_prefs:
        arquivo_prefs = Path(pasta_prefs) / "k7_gui_settings.json"
        with patch.object(app_modulo, "ARQUIVO_CONFIG", arquivo_prefs):
            janela = app_modulo.AppK7()
            janela.withdraw()
            janela.update_idletasks()
            janela.update()
            assert janela.winfo_exists()
            abas = [
                janela.abas.tab(indice, "text").strip()
                for indice in range(janela.abas.index("end"))
            ]
            assert abas == [
                "Converter",
                "Ajustes de impressão",
                "Configurações",
                "Calc",
            ], abas
            assert not hasattr(janela, "btn_config_impressao")
            assert janela.var_suporte.get() in conv.TIPOS_SUPORTE
            assert janela.var_margem.get() > 0
            assert 1 <= janela.var_quantidade.get() <= conv.QUANTIDADE_MAXIMA
            assert janela.spin_suporte_fino.winfo_manager() == "grid"
            assert janela.var_suporte_fino_mm.get() == conv.SUPORTE_FINO_PADRAO_MM
            assert janela.lnk_documentacao.cget("text") == "Documentação"
            assert not hasattr(janela, "var_formato")
            assert app_modulo.FILTROS_ARQUIVO[0] == (
                "Modelos 3MF e STL",
                "*.3mf *.stl",
            )
            assert janela.minsize() == (900, 935)
            assert janela.winfo_width() == 1000
            assert janela.winfo_height() == 935

            def descendentes(widget):
                for filho in widget.winfo_children():
                    yield filho
                    yield from descendentes(filho)

            widgets = list(descendentes(janela))
            assert not any(isinstance(w, ttk_widgets.Scale) for w in widgets)
            assert set(janela.vars_impressao) == set(conv.LIMITES_IMPRESSAO)
            textos_visiveis: set[str] = set()
            for widget in widgets:
                try:
                    textos_visiveis.add(str(widget.cget("text")))
                except Exception:
                    pass
            assert "Ajustar diâmetro do suporte" in textos_visiveis
            assert "Suportes mais finos" not in textos_visiveis
            titulos = {
                titulo for _chave, titulo, _unidade, _texto in app_modulo.CAMPOS_IMPRESSAO
            }
            assert titulos <= textos_visiveis
            assert any("Permitido: 5 a 100 %" in texto for texto in textos_visiveis)
            for sigla in ("P%", "Vp", "Vi", "Vv", "T1", "Tb", "T1b"):
                assert sigla not in textos_visiveis
            assert "Configuração de impressão" not in textos_visiveis
            assert "Filtrar arquivos:" not in textos_visiveis
            assert janela.btn_reset_impressao.cget("text") == "Reset"
            assert janela.btn_reset_impressao.winfo_manager() == "grid"

            # Alterar um ajuste salva na hora; Reset volta ao padrão e salva.
            janela.vars_impressao["fill_density"].set(55)
            janela.update()
            dados = json.loads(arquivo_prefs.read_text(encoding="utf-8"))
            assert dados["fill_density"] == 55

            janela.btn_reset_impressao.invoke()
            janela.update()
            for chave, (_minimo, _maximo, padrao) in conv.LIMITES_IMPRESSAO.items():
                assert janela.vars_impressao[chave].get() == padrao
            dados = json.loads(arquivo_prefs.read_text(encoding="utf-8"))
            assert all(
                dados[chave] == padrao
                for chave, (_minimo, _maximo, padrao) in conv.LIMITES_IMPRESSAO.items()
            )

            # Sem resultado, o botão de copiar não está gerenciado pelo grid.
            assert janela.btn_copiar_orcamento.winfo_manager() == ""

            with tempfile.TemporaryDirectory(prefix="k7_calc_gui_") as temporario:
                gcode = Path(temporario) / "teste_K7.gcode"
                gcode.write_text(
                    "; filament used [mm] = 1000.0\n"
                    "; filament used [g] = 3.0\n"
                    "; estimated printing time (normal mode) = 2h 30m 0s\n",
                    encoding="utf-8",
                )
                janela._calcular_orcamento(str(gcode))
                assert janela.orcamento is not None
                assert "PREÇO FINAL" in janela.lbl_orcamento.cget("text")
                assert abs(janela.var_tempo_calculo.get() - 2.5) < 1e-6
                assert janela.btn_copiar_orcamento.winfo_manager() == "grid"
                janela._copiar_orcamento()
                assert janela.clipboard_get() == janela.lbl_orcamento.cget("text")

                # Tempo digitado manualmente tem prioridade sobre o valor do G-code.
                janela._tempo_manual = True
                janela.var_tempo_calculo.set(5.0)
                janela._calcular_orcamento()
                assert abs(janela.orcamento.dados.horas - 5.0) < 1e-6

                # Sem tempo no G-code, usa o padrão de 1,5 h.
                sem_tempo = Path(temporario) / "sem_tempo.gcode"
                sem_tempo.write_text("; filament used [g] = 3.0\n", encoding="utf-8")
                janela._tempo_manual = False
                janela.var_tempo_calculo.set(1.5)
                janela._calcular_orcamento(str(sem_tempo))
                assert abs(janela.orcamento.dados.horas - 1.5) < 1e-6

                # O resultado não deve conter linhas em branco.
                texto = janela.lbl_orcamento.cget("text")
                assert "\n\n" not in texto, repr(texto)

                # Ao limpar o resultado, o botão desaparece junto.
                janela._limpar_orcamento()
                assert janela.btn_copiar_orcamento.winfo_manager() == ""

            # Links externos e diálogo de conclusão com crédito e doações.
            with patch.object(app_modulo.webbrowser, "open") as abrir:
                janela._abrir_documentacao()
                abrir.assert_called_once_with(app_modulo.URL_DOCUMENTACAO)
                abrir.reset_mock()
                janela._abrir_url(app_modulo.URL_LINKEDIN)
                abrir.assert_called_once_with(app_modulo.URL_LINKEDIN)

            dialogo = janela._mostrar_dialogo_sucesso(
                "C:/tmp/peca_K7.gcode", "Conversão concluída"
            )
            janela.update()
            assert dialogo.winfo_exists()
            assert dialogo.title() == "Conversão concluída"
            textos: list[str] = []
            widgets_por_texto: dict[str, object] = {}
            for filho in dialogo.winfo_children():
                for neto in filho.winfo_children():
                    try:
                        texto = str(neto.cget("text"))
                    except Exception:
                        continue
                    textos.append(texto)
                    widgets_por_texto[texto] = neto
            assert "Desenvolvedor: " in textos
            assert app_modulo.NOME_DESENVOLVEDOR in textos
            assert "Donate: " in textos
            assert "LivePix" in textos
            assert "PayPal" in textos

            with patch.object(app_modulo.webbrowser, "open") as abrir:
                widgets_por_texto["LivePix"].event_generate("<Button-1>")
                janela.update()
                abrir.assert_called_once_with(app_modulo.URL_LIVEPIX)

            janela.clipboard_clear()
            widgets_por_texto["PayPal"].event_generate("<Button-1>")
            janela.update()
            assert janela.clipboard_get() == app_modulo.EMAIL_PAYPAL
            dialogo.destroy()

            # Tamanho, quantidade, suporte e ajustes rápidos voltam como padrão
            # na próxima execução porque são gravados ao fechar (e converter).
            janela.var_tamanho.set(72.5)
            janela.var_quantidade.set(3)
            janela.var_suporte_fino.set(True)
            janela.var_suporte_fino_mm.set(1.5)
            janela.vars_impressao["fill_density"].set(40)
            janela.vars_impressao["perimeter_speed"].set(999)
            janela._ao_sair_campo_impressao("perimeter_speed")
            assert janela.vars_impressao["perimeter_speed"].get() == 60
            janela.vars_impressao["temperature"].set(210)
            janela._salvar_config()
            dados = json.loads(arquivo_prefs.read_text(encoding="utf-8"))
            assert dados["tamanho"] == 72.5
            assert dados["quantidade"] == 3
            assert dados["suporte_fino"] is True
            assert dados["suporte_fino_mm"] == 1.5
            assert dados["fill_density"] == 40
            assert dados["perimeter_speed"] == 60
            assert dados["temperature"] == 210
            assert janela._quantidade_atual() == 3
            assert janela._diametro_suporte_fino() == 1.5
            assert janela._config_impressao()["fill_density"] == 40
            janela.update()
            janela.destroy()

            # Nova execução: os valores gravados voltam como padrão, sem
            # arredondar para 0,5 mm como acontecia com a semântica antiga.
            janela2 = app_modulo.AppK7()
            janela2.withdraw()
            janela2.update_idletasks()
            janela2.update()
            assert janela2.var_tamanho.get() == 72.5
            assert janela2.var_quantidade.get() == 3
            assert janela2.var_suporte_fino.get() is True
            assert janela2.var_suporte_fino_mm.get() == 1.5
            assert janela2.vars_impressao["fill_density"].get() == 40
            assert janela2.vars_impressao["perimeter_speed"].get() == 60
            assert janela2.vars_impressao["temperature"].get() == 210

            janela2._reset_suporte_fino()
            assert janela2.var_suporte_fino.get() is False
            assert janela2.var_suporte_fino_mm.get() == conv.SUPORTE_FINO_PADRAO_MM
            janela2.update()
            janela2.destroy()

            # Migração: um valor antigo (largura de extrusão) vira diâmetro.
            dados_antigos = json.loads(arquivo_prefs.read_text(encoding="utf-8"))
            dados_antigos["suporte_fino_mm"] = 0.3
            arquivo_prefs.write_text(
                json.dumps(dados_antigos, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            janela3 = app_modulo.AppK7()
            janela3.withdraw()
            janela3.update_idletasks()
            janela3.update()
            assert janela3.var_suporte_fino_mm.get() == 0.9
            janela3.destroy()
    print("  [ok] interface ttkbootstrap constroi e fecha sem erros")
    print("  [ok] abas Configuracoes/Calc/Ajustes, diametro do suporte, Reset e documentacao presentes")
    print("  [ok] slider removido; ajustes rapidos limitados e salvos como padrao")


def main() -> None:
    print("Testando nucleo de conversao:")
    with tempfile.TemporaryDirectory(prefix="k7_teste_") as temporario:
        diretorio = Path(temporario)
        testar_conversao_completa(diretorio)
        testar_trava_seguranca(diretorio)
        testar_conversao_stl(diretorio)
        testar_multiplas_partes_e_erros(diretorio)
        testar_tipo_suporte(diretorio)
        testar_ajustes_impressao(diretorio)
        testar_quantidade(diretorio)
        testar_protecoes_travamento(diretorio)
        testar_preflight()
        testar_perfil_ini(diretorio)

    print("Testando calculo de impressao:")
    testar_calculo()

    if "--gui" in sys.argv:
        print("Testando interface:")
        testar_gui()

    print("Todos os testes passaram.")


if __name__ == "__main__":
    main()
