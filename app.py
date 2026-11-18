"""
Interface gráfica do conversor 3MF -> G-code para Easythreed K7.

Feita com ttkbootstrap (tema moderno sobre o tkinter). O trabalho pesado
fica no módulo converter_k7.py; a interface apenas coleta parâmetros,
mostra o progresso e exibe o log.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog
from tkinter import messagebox as tk_messagebox
from tkinter import ttk as tk_ttk

import ttkbootstrap as ttk
from ttkbootstrap.dialogs import Messagebox

from bootstrap import CONFIG_PAGINA, garantir_config_k7, verificar_e_instalar
from calculo import (
    CUSTOS_PADRAO,
    TEMPO_PADRAO_H,
    Custos,
    ErroCalculo,
    Orcamento,
    calcular_orcamento,
    extrair_dados_gcode,
)
from converter_k7 import (
    ALTURA_MESA_MM,
    CONFIG_PADRAO,
    LARGURA_MESA_MM,
    LIMITE_SEGURANCA_MM,
    PROFUNDIDADE_MESA_MM,
    QUANTIDADE_MAXIMA,
    SUPORTE_FINO_MAX_MM,
    SUPORTE_FINO_MIN_MM,
    SUPORTE_FINO_PADRAO_MM,
    SUPORTE_PADRAO,
    TIPOS_SUPORTE,
    AnaliseMalha,
    ResultadoConversao,
    analisar_arquivo,
    converter_3mf_para_k7,
    localizar_prusa_cli,
)

def _pasta_preferencias() -> Path:
    """Preferências ficam no perfil do usuário quando rodamos como .exe."""
    if getattr(sys, "frozen", False):
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ConversorK7"
    return Path(__file__).resolve().parent


ARQUIVO_CONFIG = _pasta_preferencias() / "k7_gui_settings.json"

TEMA_PADRAO = "bootstrap-dark"

URL_DOCUMENTACAO = (
    "https://github.com/LRamony/ConversorEasythreedK7/blob/main/README.md"
)
URL_LINKEDIN = "https://www.linkedin.com/in/ramony-sabino-5a896536/"
NOME_DESENVOLVEDOR = "Lucas Ramony"

MAX_ITENS_FILA = 500
MAX_LINHAS_LOG_GUI = 500
MAX_ITENS_POR_TICK = 150

FILTROS_ARQUIVO = {
    "3MF e STL": [
        ("Modelos 3MF e STL", "*.3mf *.stl"),
        ("Arquivos 3MF", "*.3mf"),
        ("Arquivos STL", "*.stl"),
        ("Todos os arquivos", "*.*"),
    ],
    "Somente 3MF": [
        ("Arquivos 3MF", "*.3mf"),
        ("Todos os arquivos", "*.*"),
    ],
    "Somente STL": [
        ("Arquivos STL", "*.stl"),
        ("Todos os arquivos", "*.*"),
    ],
}


def _fmt(dimensoes: tuple[float, float, float]) -> str:
    return " x ".join(f"{valor:.2f}" for valor in dimensoes)


def _eh_temporario(caminho: Path) -> bool:
    """Caminhos _MEIxxxx só existem enquanto o .exe está aberto."""
    return any(parte.startswith("_MEI") for parte in caminho.parts)


def _caminho_valido(caminho: str) -> bool:
    texto = caminho.strip().strip('"')
    return bool(texto) and Path(texto).is_file()


class AppK7(ttk.Window):
    """Janela principal do conversor."""

    def __init__(self) -> None:
        self.config_salva = self._carregar_config()
        super().__init__(
            title="Conversor 3MF / STL - Easythreed K7",
            themename=TEMA_PADRAO,
            size=(1000, 1010),
            minsize=(900, 945),
        )

        self.estilo = ttk.Style()
        self.fila: queue.Queue = queue.Queue(maxsize=MAX_ITENS_FILA)
        self.ocupado = False
        self.resultado: ResultadoConversao | None = None
        self._job_analise: str | None = None
        self._cancelar_event: threading.Event | None = None
        self._dialogo_pendente: tuple[str, str, str] | None = None
        self._analise_geracao = 0
        self._analise_ocupada = False
        self._analise_pendente: tuple[str, float, int] | None = None
        self._job_calculo: str | None = None
        self._job_fila: str | None = None
        self._calculando = False
        self._tempo_manual = False

        self.var_arquivo = tk.StringVar(value="")
        self.var_formato = tk.StringVar(value="3MF e STL")
        self.var_tamanho = tk.DoubleVar(value=60.0)
        self.var_scale = tk.DoubleVar(value=60.0)
        self.var_quantidade = tk.IntVar(value=1)
        self.var_suporte_fino = tk.BooleanVar(value=False)
        self.var_suporte_fino_mm = tk.DoubleVar(value=SUPORTE_FINO_PADRAO_MM)
        self.var_saida = tk.StringVar(value="")
        self.var_previa_saida = tk.StringVar(value="Saída: escolha um arquivo para começar")
        self.var_cli = tk.StringVar(value=str(localizar_prusa_cli() or ""))
        self.var_config = tk.StringVar(value=str(CONFIG_PADRAO))
        self.var_suporte = tk.StringVar(value=SUPORTE_PADRAO)
        self.var_status = tk.StringVar(value="Pronto para converter.")

        # Aba Calc: só a margem é obrigatória; os custos vêm com padrões.
        self.var_margem = tk.DoubleVar(value=30.0)
        self.var_tempo_calculo = tk.DoubleVar(value=TEMPO_PADRAO_H)
        self.var_gcode_calculo = tk.StringVar(value="")
        self.var_custo_kg = tk.DoubleVar(value=CUSTOS_PADRAO.preco_filamento_kg)
        self.var_preco_kwh = tk.DoubleVar(value=CUSTOS_PADRAO.preco_kwh)
        self.var_consumo_w = tk.DoubleVar(value=CUSTOS_PADRAO.consumo_w)
        self.var_desgaste = tk.DoubleVar(value=CUSTOS_PADRAO.desgaste_por_hora)
        self.var_mao_obra = tk.DoubleVar(value=CUSTOS_PADRAO.mao_obra_por_hora)
        self.var_acabamento = tk.DoubleVar(value=CUSTOS_PADRAO.acabamento_por_peca)
        self.var_embalagem = tk.DoubleVar(value=CUSTOS_PADRAO.embalagem_por_peca)
        self.orcamento: Orcamento | None = None

        self._construir_interface()
        self._aplicar_config_salva()

        # Qualquer alteração nos valores recalcula automaticamente.
        for variavel in (
            self.var_margem,
            self.var_custo_kg,
            self.var_preco_kwh,
            self.var_consumo_w,
            self.var_desgaste,
            self.var_mao_obra,
            self.var_acabamento,
            self.var_embalagem,
        ):
            variavel.trace_add("write", self._ao_mudar_calculo)
        self.var_tempo_calculo.trace_add("write", self._ao_mudar_tempo)
        self.var_quantidade.trace_add("write", self._ao_mudar_quantidade)

        self.protocol("WM_DELETE_WINDOW", self._ao_fechar)
        self._job_fila = self.after(100, self._processar_fila)

    def _cancelar_temporizadores(self) -> None:
        """Evita callbacks de `after` depois que a janela foi fechada."""
        for atributo in ("_job_fila", "_job_calculo", "_job_analise"):
            job = getattr(self, atributo, None)
            if job is None:
                continue
            try:
                self.after_cancel(job)
            except (tk.TclError, ValueError):
                pass
            setattr(self, atributo, None)

    def destroy(self) -> None:
        self._cancelar_temporizadores()
        super().destroy()

    def _enfileirar(self, item: tuple) -> None:
        """Envia mensagens da thread para a GUI sem deixar a fila crescer sem fim."""
        try:
            self.fila.put_nowait(item)
        except queue.Full:
            try:
                self.fila.get_nowait()
            except queue.Empty:
                pass
            try:
                self.fila.put_nowait(item)
            except queue.Full:
                pass

    # ------------------------------------------------------------------
    # Persistência das preferências
    # ------------------------------------------------------------------
    @staticmethod
    def _carregar_config() -> dict:
        try:
            if ARQUIVO_CONFIG.is_file():
                dados = json.loads(ARQUIVO_CONFIG.read_text(encoding="utf-8"))
                return dados if isinstance(dados, dict) else {}
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    def _salvar_config(self) -> None:
        config = self.var_config.get().strip()
        if not config or _eh_temporario(Path(config)):
            config = str(CONFIG_PADRAO)
        dados = {
            "formato": self.var_formato.get(),
            "cli": self.var_cli.get().strip(),
            "config": config,
            "saida": self.var_saida.get().strip(),
            "tamanho": self.var_tamanho.get(),
            "quantidade": self.var_quantidade.get(),
            "suporte": self.var_suporte.get(),
            "suporte_fino": self.var_suporte_fino.get(),
            "suporte_fino_mm": self.var_suporte_fino_mm.get(),
            "margem": self.var_margem.get(),
            "tempo_calculo": self.var_tempo_calculo.get(),
            "gcode_calculo": self.var_gcode_calculo.get().strip(),
            "custo_kg": self.var_custo_kg.get(),
            "preco_kwh": self.var_preco_kwh.get(),
            "consumo_w": self.var_consumo_w.get(),
            "desgaste": self.var_desgaste.get(),
            "mao_obra": self.var_mao_obra.get(),
            "acabamento": self.var_acabamento.get(),
            "embalagem": self.var_embalagem.get(),
        }
        try:
            ARQUIVO_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            ARQUIVO_CONFIG.write_text(
                json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass

    def _aplicar_config_salva(self) -> None:
        formato = self.config_salva.get("formato", "3MF e STL")
        if formato in FILTROS_ARQUIVO:
            self.var_formato.set(formato)
        self.var_cli.set(self.config_salva.get("cli", self.var_cli.get()))
        config_salva = str(self.config_salva.get("config", ""))
        self.var_config.set(
            config_salva if _caminho_valido(config_salva) else str(CONFIG_PADRAO)
        )
        self.var_saida.set(self.config_salva.get("saida", ""))

        suporte = str(self.config_salva.get("suporte", ""))
        self.var_suporte.set(suporte if suporte in TIPOS_SUPORTE else SUPORTE_PADRAO)
        self.var_suporte_fino.set(bool(self.config_salva.get("suporte_fino", False)))
        try:
            espessura = float(self.config_salva["suporte_fino_mm"])
            self.var_suporte_fino_mm.set(
                min(max(espessura, SUPORTE_FINO_MIN_MM), SUPORTE_FINO_MAX_MM)
            )
        except (KeyError, TypeError, ValueError, tk.TclError):
            self.var_suporte_fino_mm.set(SUPORTE_FINO_PADRAO_MM)

        def restaurar(chave: str, variavel: tk.Variable) -> None:
            try:
                variavel.set(float(self.config_salva[chave]))
            except (KeyError, TypeError, ValueError, tk.TclError):
                pass

        # O último tamanho usado vira o padrão da próxima execução.
        restaurar("tamanho", self.var_tamanho)
        try:
            tamanho = min(max(float(self.var_tamanho.get()), 1.0), LIMITE_SEGURANCA_MM)
            self.var_tamanho.set(tamanho)
            self.var_scale.set(tamanho)
        except (TypeError, ValueError, tk.TclError):
            pass

        try:
            quantidade = int(self.config_salva["quantidade"])
            self.var_quantidade.set(min(max(quantidade, 1), QUANTIDADE_MAXIMA))
        except (KeyError, TypeError, ValueError, tk.TclError):
            pass

        restaurar("margem", self.var_margem)
        restaurar("tempo_calculo", self.var_tempo_calculo)
        restaurar("custo_kg", self.var_custo_kg)
        restaurar("preco_kwh", self.var_preco_kwh)
        restaurar("consumo_w", self.var_consumo_w)
        restaurar("desgaste", self.var_desgaste)
        restaurar("mao_obra", self.var_mao_obra)
        restaurar("acabamento", self.var_acabamento)
        restaurar("embalagem", self.var_embalagem)
        self.var_gcode_calculo.set(str(self.config_salva.get("gcode_calculo", "")))

        self._atualizar_cores_log()
        self._atualizar_previa_saida()

    # ------------------------------------------------------------------
    # Construção da interface
    # ------------------------------------------------------------------
    def _construir_interface(self) -> None:
        principal = ttk.Frame(self, padding=14)
        principal.pack(fill="both", expand=True)
        principal.columnconfigure(0, weight=1)
        principal.rowconfigure(1, weight=1)

        self._construir_cabecalho(principal)

        abas = ttk.Notebook(principal)
        abas.grid(row=1, column=0, sticky="nsew")
        self.abas = abas

        aba_conversao = ttk.Frame(abas, padding=12)
        aba_config = ttk.Frame(abas, padding=12)
        aba_calc = ttk.Frame(abas, padding=12)
        abas.add(aba_conversao, text="  Converter  ")
        abas.add(aba_config, text="  Configurações  ")
        abas.add(aba_calc, text="  Calc  ")

        self._construir_aba_conversao(aba_conversao)
        self._construir_aba_config(aba_config)
        self._construir_aba_calc(aba_calc)
        self._construir_log(principal)
        self._construir_acoes(principal)

        rodape = ttk.Frame(principal)
        rodape.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        rodape.columnconfigure(0, weight=1)
        ttk.Label(
            rodape,
            textvariable=self.var_status,
            bootstyle="secondary",
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        self.lnk_documentacao = ttk.Label(
            rodape,
            text="Documentação",
            bootstyle="primary",
            cursor="hand2",
            font=("Segoe UI", 9, "underline"),
        )
        self.lnk_documentacao.pack(side="right", padx=(12, 0))
        self.lnk_documentacao.bind(
            "<Button-1>", lambda _evento: self._abrir_documentacao()
        )

    def _construir_cabecalho(self, principal: ttk.Frame) -> None:
        cabecalho = ttk.Frame(principal)
        cabecalho.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        cabecalho.columnconfigure(1, weight=1)

        ttk.Label(
            cabecalho,
            text="Conversor 3MF / STL  →  Easythreed K7",
            font=("Segoe UI", 18, "bold"),
            bootstyle="primary",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            cabecalho,
            text=f"Redimensiona, centraliza e fatia .3mf ou .stl para a mesa "
            f"{LARGURA_MESA_MM:g} x {PROFUNDIDADE_MESA_MM:g} x {ALTURA_MESA_MM:g} mm",
            font=("Segoe UI", 10),
            bootstyle="secondary",
        ).grid(row=1, column=0, sticky="w")

    def _cartao(self, parent: ttk.Frame, titulo: str) -> ttk.Labelframe:
        cartao = ttk.Labelframe(parent, text=f"  {titulo}  ", padding=12, bootstyle="info")
        cartao.pack(fill="x", pady=(0, 10))
        return cartao

    def _construir_aba_conversao(self, aba: ttk.Frame) -> None:
        cartao_arquivo = self._cartao(aba, "1. Arquivo de entrada")
        cartao_arquivo.columnconfigure(0, weight=1)
        entrada = ttk.Entry(cartao_arquivo, textvariable=self.var_arquivo)
        entrada.grid(row=0, column=0, sticky="ew")
        entrada.bind("<Return>", lambda _evento: self._agendar_analise())
        ttk.Button(
            cartao_arquivo,
            text="Procurar...",
            bootstyle="primary-outline",
            command=self._escolher_arquivo,
        ).grid(row=0, column=1, padx=(8, 0))
        ttk.Label(
            cartao_arquivo, text="Filtrar arquivos:", bootstyle="secondary"
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(
            cartao_arquivo,
            textvariable=self.var_formato,
            values=list(FILTROS_ARQUIVO),
            state="readonly",
            width=14,
        ).grid(row=1, column=1, sticky="e", pady=(6, 0))

        cartao_tamanho = self._cartao(aba, "2. Tamanho máximo da peça")
        cartao_tamanho.columnconfigure(2, weight=1)
        ttk.Label(cartao_tamanho, text="Medida máxima (mm):").grid(row=0, column=0, sticky="w")
        spin = ttk.Spinbox(
            cartao_tamanho,
            from_=1.0,
            to=LIMITE_SEGURANCA_MM,
            increment=1.0,
            textvariable=self.var_tamanho,
            width=9,
        )
        spin.grid(row=0, column=1, sticky="w", padx=(10, 0))
        spin.bind("<KeyRelease>", self._ao_mudar_tamanho)
        spin.bind("<FocusOut>", self._ao_mudar_tamanho)

        escala = ttk.Scale(
            cartao_tamanho,
            from_=10.0,
            to=LIMITE_SEGURANCA_MM,
            orient="horizontal",
            variable=self.var_scale,
            command=self._ao_mover_escala,
            bootstyle="primary",
        )
        ttk.Label(
            cartao_tamanho, text="Quantidade de peças:", bootstyle="primary"
        ).grid(row=0, column=2, sticky="e", padx=(24, 0))
        ttk.Spinbox(
            cartao_tamanho,
            from_=1,
            to=QUANTIDADE_MAXIMA,
            increment=1,
            textvariable=self.var_quantidade,
            width=8,
        ).grid(row=0, column=3, sticky="w", padx=(8, 0))

        escala.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(10, 2))
        ttk.Label(
            cartao_tamanho,
            text=f"Trava de segurança: qualquer valor acima de {LIMITE_SEGURANCA_MM:g} mm "
            "é limitado automaticamente antes de fatiar.\n"
            "Ao aumentar a quantidade, a escala é reduzida para que todas as peças "
            "caibam. O último tamanho é lembrado na próxima execução.",
            bootstyle="warning",
            justify="left",
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))

        cartao_suporte = self._cartao(aba, "3. Tipo de suporte")
        cartao_suporte.columnconfigure(1, weight=1)
        ttk.Label(cartao_suporte, text="Árvore = orgânico:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            cartao_suporte,
            textvariable=self.var_suporte,
            values=list(TIPOS_SUPORTE),
            state="readonly",
            width=28,
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Checkbutton(
            cartao_suporte,
            text="Suportes mais finos",
            variable=self.var_suporte_fino,
            bootstyle="success",
        ).grid(row=0, column=2, sticky="w", padx=(16, 0))
        self.spin_suporte_fino = ttk.Spinbox(
            cartao_suporte,
            from_=SUPORTE_FINO_MIN_MM,
            to=SUPORTE_FINO_MAX_MM,
            increment=0.05,
            textvariable=self.var_suporte_fino_mm,
            width=6,
        )
        self.spin_suporte_fino.grid(row=0, column=3, sticky="w", padx=(8, 0))
        ttk.Label(cartao_suporte, text="mm").grid(row=0, column=4, sticky="w", padx=(4, 0))
        ttk.Button(
            cartao_suporte,
            text="Reset",
            bootstyle="secondary-outline",
            command=self._reset_suporte_fino,
        ).grid(row=0, column=5, sticky="w", padx=(10, 0))

        cartao_saida = self._cartao(aba, "4. Arquivo de saída")
        cartao_saida.columnconfigure(0, weight=1)
        ttk.Label(cartao_saida, text="Pasta de saída (em branco = mesma pasta do modelo)").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Entry(cartao_saida, textvariable=self.var_saida).grid(
            row=1, column=0, sticky="ew", pady=(6, 0)
        )
        ttk.Button(
            cartao_saida,
            text="Pasta...",
            bootstyle="secondary-outline",
            command=self._escolher_pasta,
        ).grid(row=1, column=1, padx=(8, 0), pady=(6, 0))
        ttk.Label(
            cartao_saida,
            textvariable=self.var_previa_saida,
            bootstyle="secondary",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        cartao_previa = self._cartao(aba, "Prévia da peça")
        self.lbl_dimensoes = ttk.Label(
            cartao_previa, text="Dimensões atuais: —", bootstyle="primary"
        )
        self.lbl_dimensoes.grid(row=0, column=0, sticky="w")
        self.lbl_escala = ttk.Label(
            cartao_previa, text="Fator de escala: —", bootstyle="secondary"
        )
        self.lbl_escala.grid(row=1, column=0, sticky="w", pady=(3, 0))
        self.lbl_final = ttk.Label(cartao_previa, text="Após o ajuste: —", bootstyle="secondary")
        self.lbl_final.grid(row=2, column=0, sticky="w", pady=(3, 0))

        self.var_arquivo.trace_add("write", lambda *_: self._atualizar_previa_saida())
        self.var_saida.trace_add("write", lambda *_: self._atualizar_previa_saida())

    def _construir_aba_config(self, aba: ttk.Frame) -> None:
        cartao_cli = self._cartao(aba, "PrusaSlicer (motor de fatiamento)")
        cartao_cli.columnconfigure(0, weight=1)
        ttk.Label(
            cartao_cli,
            text="Caminho do executável usado para gerar o G-code:",
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Entry(cartao_cli, textvariable=self.var_cli).grid(
            row=1, column=0, columnspan=3, sticky="ew", pady=(6, 0)
        )
        ttk.Button(
            cartao_cli,
            text="Procurar...",
            bootstyle="primary-outline",
            command=self._escolher_cli,
        ).grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Button(
            cartao_cli,
            text="Detectar automaticamente",
            bootstyle="secondary-outline",
            command=self._detectar_cli,
        ).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(8, 0))
        ttk.Label(
            cartao_cli,
            text="Geralmente: C:\\Program Files\\Prusa3D\\PrusaSlicer\\prusa-slicer-console.exe",
            bootstyle="secondary",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))

        cartao_perfil = self._cartao(aba, "Perfil da impressora")
        cartao_perfil.columnconfigure(0, weight=1)
        ttk.Label(cartao_perfil, text="Arquivo .ini com os limites da K7:").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Entry(cartao_perfil, textvariable=self.var_config).grid(
            row=1, column=0, sticky="ew", pady=(6, 0)
        )
        ttk.Button(
            cartao_perfil,
            text="Procurar...",
            bootstyle="secondary-outline",
            command=self._escolher_config,
        ).grid(row=1, column=1, padx=(8, 0), pady=(6, 0))
        ttk.Button(
            cartao_perfil,
            text="Abrir perfil",
            bootstyle="secondary-outline",
            command=lambda: self._abrir_no_sistema(Path(self.var_config.get().strip())),
        ).grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Button(
            cartao_perfil,
            text="Baixar perfil",
            bootstyle="success-outline",
            command=self._baixar_perfil,
        ).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(8, 0))
        ttk.Label(
            cartao_perfil,
            text="Se o perfil faltar, ele é recriado automaticamente. "
            f"Perfis alternativos: {CONFIG_PAGINA}",
            bootstyle="secondary",
            wraplength=620,
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        cartao_limites = self._cartao(aba, "Limites aplicados no G-code")
        ttk.Label(
            cartao_limites,
            text=f"Mesa: {LARGURA_MESA_MM:g} x {PROFUNDIDADE_MESA_MM:g} x "
            f"{ALTURA_MESA_MM:g} mm  |  Tamanho útil: {LIMITE_SEGURANCA_MM:g} mm\n"
            "Bico: 0,4 mm  |  Filamento: 1,75 mm  |  Hotend: 200 °C (1ª camada) / 195 °C\n"
            "Mesa: 0 °C  |  Camada: 0,2 mm  |  Preenchimento: 15%  |  Suporte: escolhido na aba Converter",
            bootstyle="secondary",
            justify="left",
        ).pack(anchor="w")

    def _construir_aba_calc(self, aba: ttk.Frame) -> None:
        # O resultado pode ficar longo; um frame com rolagem evita esconder
        # os últimos valores em telas menores.
        conteudo = ttk.ScrolledFrame(aba, bootstyle="round")
        conteudo.pack(fill="both", expand=True)
        pai = conteudo

        cartao_entrada = self._cartao(pai, "1. G-code e margem de lucro")
        cartao_entrada.columnconfigure(1, weight=1)
        ttk.Label(cartao_entrada, text="G-code:").grid(row=0, column=0, sticky="w")
        ttk.Entry(cartao_entrada, textvariable=self.var_gcode_calculo).grid(
            row=0, column=1, sticky="ew"
        )
        ttk.Button(
            cartao_entrada,
            text="Procurar...",
            bootstyle="secondary-outline",
            command=self._escolher_gcode_calculo,
        ).grid(row=0, column=2, sticky="e", padx=(8, 0))
        campos = ttk.Frame(cartao_entrada)
        campos.grid(row=1, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Label(campos, text="Margem de lucro (%):", bootstyle="primary").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Spinbox(
            campos,
            from_=0.0,
            to=1000.0,
            increment=5.0,
            textvariable=self.var_margem,
            width=10,
        ).grid(row=0, column=1, sticky="w", padx=(8, 24))
        ttk.Label(campos, text="Tempo de impressão (h):", bootstyle="primary").grid(
            row=0, column=2, sticky="w"
        )
        ttk.Spinbox(
            campos,
            from_=0.01,
            to=1000.0,
            increment=0.25,
            textvariable=self.var_tempo_calculo,
            width=10,
        ).grid(row=0, column=3, sticky="w", padx=(8, 0))
        ttk.Label(
            cartao_entrada,
            text="O cálculo é automático. Tempo e filamento são lidos do G-code; "
            "se o arquivo não informar o tempo, o valor ao lado (padrão 1,5 h) é "
            "usado e pode ser editado. Os custos já vêm com valores padrão.",
            bootstyle="secondary",
            wraplength=620,
            justify="left",
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))

        cartao_custos = self._cartao(pai, "2. Custos usados no cálculo (padrões editáveis)")
        campos = (
            ("Filamento (R$/kg):", self.var_custo_kg),
            ("Energia (R$/kWh):", self.var_preco_kwh),
            ("Consumo da K7 (W):", self.var_consumo_w),
            ("Desgaste (R$/h):", self.var_desgaste),
            ("Mão de obra (R$/h):", self.var_mao_obra),
            ("Acabamento (R$/peça):", self.var_acabamento),
            ("Embalagem (R$/peça):", self.var_embalagem),
        )
        for indice, (texto, variavel) in enumerate(campos):
            linha, coluna = divmod(indice, 2)
            ttk.Label(cartao_custos, text=texto).grid(
                row=linha, column=coluna * 2, sticky="w", pady=(4, 0)
            )
            ttk.Spinbox(
                cartao_custos,
                from_=0.0,
                to=100000.0,
                increment=0.5,
                textvariable=variavel,
                width=10,
            ).grid(
                row=linha,
                column=coluna * 2 + 1,
                sticky="w",
                padx=(8, 16),
                pady=(4, 0),
            )

        cartao_resultado = self._cartao(pai, "3. Resultado")
        cartao_resultado.columnconfigure(0, weight=1)
        self.lbl_orcamento = ttk.Label(
            cartao_resultado,
            text="Escolha um G-code para ver o orçamento.",
            font=("Consolas", 10),
            justify="left",
            bootstyle="secondary",
        )
        self.lbl_orcamento.grid(row=0, column=0, sticky="nw")
        self.btn_copiar_orcamento = ttk.Button(
            cartao_resultado,
            text="Copiar resultado",
            bootstyle="secondary-outline",
            command=self._copiar_orcamento,
        )
        self.btn_copiar_orcamento.grid(row=0, column=1, sticky="ne", padx=(12, 0))
        self.btn_copiar_orcamento.grid_remove()

    def _construir_log(self, principal: ttk.Frame) -> None:
        quadro = ttk.Labelframe(principal, text="  Log de execução  ", padding=6, bootstyle="info")
        quadro.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        quadro.columnconfigure(0, weight=1)
        quadro.rowconfigure(0, weight=1)

        self.texto_log = ttk.ScrolledText(quadro, height=6, wrap="word", bootstyle="default")
        self.texto_log.grid(row=0, column=0, sticky="nsew")
        self.texto_log.configure(state="disabled")

    def _construir_acoes(self, principal: ttk.Frame) -> None:
        acoes = ttk.Frame(principal)
        acoes.grid(row=3, column=0, sticky="ew", pady=(10, 0))

        self.btn_converter = ttk.Button(
            acoes,
            text="Converter para G-code",
            bootstyle="success",
            command=self._iniciar_conversao,
        )
        self.btn_converter.pack(side="left")

        self.btn_cancelar = ttk.Button(
            acoes,
            text="Cancelar",
            bootstyle="danger-outline",
            command=self._cancelar_conversao,
            state="disabled",
        )
        self.btn_cancelar.pack(side="left", padx=(8, 0))

        ttk.Button(
            acoes,
            text="Limpar log",
            bootstyle="secondary-outline",
            command=self._limpar_log,
        ).pack(side="left", padx=(8, 0))

        self.btn_abrir = ttk.Button(
            acoes,
            text="Abrir G-code",
            bootstyle="info-outline",
            command=self._abrir_gcode,
            state="disabled",
        )
        self.btn_abrir.pack(side="left", padx=(8, 0))

        self.btn_pasta = ttk.Button(
            acoes,
            text="Abrir pasta",
            bootstyle="info-outline",
            command=self._abrir_pasta,
            state="disabled",
        )
        self.btn_pasta.pack(side="left", padx=(8, 0))

        self.progresso = ttk.Progressbar(
            acoes, mode="indeterminate", bootstyle="success-striped"
        )
        self.progresso.pack(side="right", fill="x", expand=True, padx=(14, 0))

    # ------------------------------------------------------------------
    # Eventos da interface
    # ------------------------------------------------------------------
    def _ao_mover_escala(self, valor: str) -> None:
        try:
            self.var_tamanho.set(round(float(valor), 1))
        except ValueError:
            return
        self._agendar_analise()

    def _ao_mudar_tamanho(self, _evento=None) -> None:
        try:
            valor = float(self.var_tamanho.get())
        except (ValueError, tk.TclError):
            return
        valor = min(max(valor, 1.0), LIMITE_SEGURANCA_MM)
        self.var_scale.set(valor)
        self._agendar_analise()

    def _ao_mudar_quantidade(self, *_eventos) -> None:
        """Recalcula a prévia quando a quantidade de peças muda."""
        try:
            valor = int(self.var_quantidade.get())
        except (ValueError, TypeError, tk.TclError):
            return
        limitado = min(max(valor, 1), QUANTIDADE_MAXIMA)
        if limitado != valor:
            self.var_quantidade.set(limitado)  # o trace chama esta função de novo
            return
        self._agendar_analise()

    def _reset_suporte_fino(self) -> None:
        """Volta ao estado de fábrica: opção desligada e 0,3 mm."""
        self.var_suporte_fino.set(False)
        self.var_suporte_fino_mm.set(SUPORTE_FINO_PADRAO_MM)
        self._escrever_log(
            "success",
            f"Suporte fino restaurado para o padrão ({SUPORTE_FINO_PADRAO_MM:g} mm, desligado).",
        )

    def _abrir_url(self, url: str) -> None:
        try:
            webbrowser.open(url)
        except Exception as exc:  # navegador ausente não pode derrubar a GUI
            self._escrever_log("error", f"Não foi possível abrir o link: {exc}")

    def _abrir_documentacao(self) -> None:
        self._abrir_url(URL_DOCUMENTACAO)

    def _escolher_arquivo(self) -> None:
        formato = self.var_formato.get()
        filtros = FILTROS_ARQUIVO.get(formato, FILTROS_ARQUIVO["3MF e STL"])
        arquivo = filedialog.askopenfilename(
            parent=self, title="Selecione o arquivo do modelo", filetypes=filtros
        )
        if arquivo:
            self.var_arquivo.set(arquivo)
            self._agendar_analise()

    def _escolher_pasta(self) -> None:
        pasta = filedialog.askdirectory(parent=self, title="Pasta onde salvar o G-code")
        if pasta:
            self.var_saida.set(pasta)

    def _escolher_cli(self) -> None:
        atual = Path(self.var_cli.get().strip())
        inicial = str(atual.parent) if atual.parent.is_dir() else None
        executavel = filedialog.askopenfilename(
            parent=self,
            title="Localize o prusa-slicer-console.exe",
            filetypes=[("Executável", "*.exe"), ("Todos os arquivos", "*.*")],
            initialdir=inicial,
        )
        if executavel:
            self.var_cli.set(executavel)

    def _detectar_cli(self) -> None:
        encontrado = localizar_prusa_cli()
        if encontrado:
            self.var_cli.set(str(encontrado))
            self._escrever_log("success", f"PrusaSlicer detectado: {encontrado}")
        else:
            Messagebox.show_warning(
                "Não encontramos o PrusaSlicer nos locais padrão. "
                "Use o botão 'Procurar...' para informar o executável.",
                "Não encontrado",
                parent=self,
            )

    def _escolher_config(self) -> None:
        atual = Path(self.var_config.get().strip())
        inicial = str(atual.parent) if atual.parent.is_dir() else None
        arquivo = filedialog.askopenfilename(
            parent=self,
            title="Selecione o perfil .ini da K7",
            filetypes=[("Arquivos INI", "*.ini"), ("Todos os arquivos", "*.*")],
            initialdir=inicial,
        )
        if arquivo:
            self.var_config.set(arquivo)

    def _baixar_perfil(self) -> None:
        aviso = garantir_config_k7(lambda _mensagem: None)
        if aviso:
            Messagebox.show_warning(
                f"{aviso.nome}\n\n{aviso.motivo}\n\nBaixe em: {aviso.url}",
                "Não foi possível obter o perfil",
                parent=self,
            )
            return
        destino = filedialog.asksaveasfilename(
            parent=self,
            title="Salvar cópia do perfil da K7",
            defaultextension=".ini",
            filetypes=[("Arquivos INI", "*.ini")],
            initialfile=CONFIG_PADRAO.name,
        )
        if destino:
            try:
                shutil.copyfile(CONFIG_PADRAO, destino)
                self._escrever_log("success", f"Perfil salvo em: {destino}")
            except OSError as exc:
                self._escrever_log("error", f"Falha ao salvar perfil: {exc}")

    # ------------------------------------------------------------------
    # Aba Calc: orçamento a partir do G-code
    # ------------------------------------------------------------------
    def _ao_mudar_tempo(self, *_args) -> None:
        """Tempo digitado pelo usuário tem prioridade sobre o do G-code."""
        if self._calculando:
            return
        self._tempo_manual = True
        self._ao_mudar_calculo()

    def _ao_mudar_calculo(self, *_args) -> None:
        """Agenda um novo cálculo quando margem, tempo ou custos mudam."""
        if self._calculando:
            return
        if self._job_calculo is not None:
            try:
                self.after_cancel(self._job_calculo)
            except tk.TclError:
                pass
        self._job_calculo = self.after(300, self._recalcular_se_possivel)

    def _recalcular_se_possivel(self) -> None:
        self._job_calculo = None
        caminho = self.var_gcode_calculo.get().strip().strip('"')
        if caminho and Path(caminho).is_file():
            self._calcular_orcamento(caminho)

    def _escolher_gcode_calculo(self) -> None:
        atual = self.var_gcode_calculo.get().strip().strip('"')
        inicial = str(Path(atual).parent) if atual and Path(atual).parent.is_dir() else None
        arquivo = filedialog.askopenfilename(
            parent=self,
            title="Selecione o G-code para calcular",
            filetypes=[("G-code", "*.gcode *.gco"), ("Todos os arquivos", "*.*")],
            initialdir=inicial,
        )
        if arquivo:
            self._tempo_manual = False
            self.var_gcode_calculo.set(arquivo)
            self.after(50, self._calcular_orcamento)

    def _custos_atuais(self) -> Custos:
        def valor(variavel: tk.DoubleVar, padrao: float) -> float:
            try:
                return float(variavel.get())
            except (ValueError, TypeError, tk.TclError):
                return padrao

        return Custos(
            preco_filamento_kg=valor(
                self.var_custo_kg, CUSTOS_PADRAO.preco_filamento_kg
            ),
            preco_kwh=valor(self.var_preco_kwh, CUSTOS_PADRAO.preco_kwh),
            consumo_w=valor(self.var_consumo_w, CUSTOS_PADRAO.consumo_w),
            desgaste_por_hora=valor(self.var_desgaste, CUSTOS_PADRAO.desgaste_por_hora),
            mao_obra_por_hora=valor(self.var_mao_obra, CUSTOS_PADRAO.mao_obra_por_hora),
            acabamento_por_peca=valor(
                self.var_acabamento, CUSTOS_PADRAO.acabamento_por_peca
            ),
            embalagem_por_peca=valor(self.var_embalagem, CUSTOS_PADRAO.embalagem_por_peca),
        )

    def _calcular_orcamento(self, arquivo: str | None = None) -> None:
        if self._calculando:
            return
        caminho = arquivo or self.var_gcode_calculo.get().strip().strip('"')
        if not caminho:
            if arquivo is not None:
                Messagebox.show_warning(
                    "Escolha um G-code para calcular o preço.",
                    "G-code não informado",
                    parent=self,
                )
            return

        try:
            horas_padrao = float(self.var_tempo_calculo.get())
        except (ValueError, TypeError, tk.TclError):
            horas_padrao = TEMPO_PADRAO_H

        self._calculando = True
        try:
            dados = extrair_dados_gcode(caminho, horas_padrao=horas_padrao)
            if self._tempo_manual:
                # O usuário digitou um valor: ele vale mais que o do G-code.
                dados = replace(dados, horas=horas_padrao, tempo_encontrado=False)
            elif dados.tempo_encontrado:
                self.var_tempo_calculo.set(round(dados.horas, 4))
            else:
                self._escrever_log(
                    "warn",
                    "Calc: tempo não encontrado no G-code; usando o valor editável "
                    f"de {dados.horas:g} h.",
                )
            margem = float(self.var_margem.get())
            orcamento = calcular_orcamento(dados, margem, self._custos_atuais())
        except (ErroCalculo, ValueError, TypeError, tk.TclError) as exc:
            self._limpar_orcamento()
            self._escrever_log("warn", f"Calc: {exc}")
            Messagebox.show_error(str(exc), "Não foi possível calcular", parent=self)
            return
        finally:
            self._calculando = False

        self.orcamento = orcamento
        self.var_gcode_calculo.set(str(dados.arquivo))
        self._mostrar_orcamento(orcamento)
        self._escrever_log(
            "success", f"Calc: preço final {self._fmt_reais(orcamento.preco_final)}"
        )

    @staticmethod
    def _fmt_reais(valor: float) -> str:
        texto = f"{valor:,.2f}"
        return "R$ " + texto.replace(",", "@").replace(".", ",").replace("@", ".")

    def _mostrar_orcamento(self, orcamento: Orcamento) -> None:
        origem_tempo = "estimado" if orcamento.dados.tempo_encontrado else "informado"
        linhas = [
            f"G-code: {orcamento.dados.arquivo.name}",
            f"Tempo {origem_tempo}: {orcamento.dados.horas:.2f} h",
            f"Filamento: {orcamento.dados.filamento_g:.1f} g "
            f"({orcamento.dados.filamento_mm:.0f} mm)",
            f"Margem aplicada: {orcamento.margem_percentual:g} %",
            "Custo de produção:",
        ]
        for nome, valor in orcamento.linhas():
            linhas.append(f"  {nome:<24} {self._fmt_reais(valor)}")
        linhas.extend(
            [
                "  " + "-" * 38,
                f"  {'Custo total':<24} {self._fmt_reais(orcamento.custo_total)}",
                f"  {'PREÇO FINAL':<24} {self._fmt_reais(orcamento.preco_final)}",
                f"  {'Lucro':<24} {self._fmt_reais(orcamento.lucro)}",
            ]
        )
        self.lbl_orcamento.configure(text="\n".join(linhas), bootstyle="primary")
        self.btn_copiar_orcamento.grid()

    def _copiar_orcamento(self) -> None:
        """Copia o resultado atual para a área de transferência."""
        if self.orcamento is None:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(self.lbl_orcamento.cget("text"))
            self.update_idletasks()
        except tk.TclError as exc:
            self._escrever_log("error", f"Calc: não foi possível copiar: {exc}")
            return
        self._escrever_log("success", "Calc: resultado copiado.")

    def _limpar_orcamento(self) -> None:
        self.orcamento = None
        self.lbl_orcamento.configure(
            text="Escolha um G-code para ver o orçamento.",
            bootstyle="secondary",
        )
        self.btn_copiar_orcamento.grid_remove()

    def _ao_fechar(self) -> None:
        if self._cancelar_event is not None:
            self._cancelar_event.set()
        self._dialogo_pendente = None
        self._salvar_config()
        self.destroy()

    # ------------------------------------------------------------------
    # Prévia assíncrona
    # ------------------------------------------------------------------
    def _agendar_analise(self) -> None:
        if self._job_analise is not None:
            try:
                self.after_cancel(self._job_analise)
            except (ValueError, tk.TclError):
                pass
        self._analise_geracao += 1
        self._job_analise = self.after(350, self._analisar)

    def _analisar(self) -> None:
        arquivo = self.var_arquivo.get().strip().strip('"')
        if not arquivo or not Path(arquivo).is_file():
            self._limpar_previa()
            return

        parametros = (arquivo, self._medida_atual(), self._quantidade_atual())

        # Uma única thread de prévia por vez: arrastar o slider não pode criar
        # dezenas de leituras simultâneas da mesma malha.
        if self._analise_ocupada:
            self._analise_pendente = parametros
            return

        self.lbl_dimensoes.configure(text="Dimensões atuais: calculando...")
        self._analise_ocupada = True
        threading.Thread(
            target=self._worker_analise,
            args=(*parametros, self._analise_geracao),
            daemon=True,
        ).start()

    def _worker_analise(
        self, arquivo: str, medida: float, quantidade: int, geracao: int
    ) -> None:
        try:
            analise = analisar_arquivo(arquivo, medida, quantidade=quantidade)
        except Exception as exc:  # a prévia nunca deve derrubar a interface
            self._enfileirar(("analise_erro", geracao, str(exc)))
        else:
            self._enfileirar(("analise", geracao, analise))

    def _mostrar_analise(self, analise: AnaliseMalha) -> None:
        self.lbl_dimensoes.configure(
            text=f"Dimensões atuais: {_fmt(analise.dimensoes_originais)} mm"
        )
        detalhe = f"{analise.partes} parte(s)"
        if analise.quantidade > 1:
            detalhe += f", {analise.quantidade} peça(s)"
        self.lbl_escala.configure(
            text=f"Fator de escala: {analise.fator_escala:.4f}x  ({detalhe})"
        )
        if analise.quantidade > 1:
            por_peca = tuple(
                float(valor) * analise.fator_escala
                for valor in analise.dimensoes_originais
            )
            self.lbl_final.configure(
                text=f"Conjunto {analise.colunas}x{analise.linhas}: "
                f"{_fmt(analise.dimensoes_finais)} mm\n"
                f"Cada peça: {_fmt(por_peca)} mm"
            )
        else:
            self.lbl_final.configure(
                text=f"Após o ajuste: {_fmt(analise.dimensoes_finais)} mm"
            )

    def _limpar_previa(self) -> None:
        self.lbl_dimensoes.configure(text="Dimensões atuais: —")
        self.lbl_escala.configure(text="Fator de escala: —")
        self.lbl_final.configure(text="Após o ajuste: —")

    # ------------------------------------------------------------------
    # Conversão
    # ------------------------------------------------------------------
    def _medida_atual(self) -> float:
        try:
            return float(self.var_tamanho.get())
        except (ValueError, tk.TclError):
            return LIMITE_SEGURANCA_MM

    def _quantidade_atual(self) -> int:
        try:
            return min(max(int(self.var_quantidade.get()), 1), QUANTIDADE_MAXIMA)
        except (ValueError, TypeError, tk.TclError):
            return 1

    def _espessura_suporte_fino(self) -> float:
        try:
            valor = float(self.var_suporte_fino_mm.get())
        except (ValueError, TypeError, tk.TclError):
            return SUPORTE_FINO_PADRAO_MM
        return min(
            max(round(valor, 2), SUPORTE_FINO_MIN_MM), SUPORTE_FINO_MAX_MM
        )

    def _iniciar_conversao(self) -> None:
        if self.ocupado:
            return

        arquivo = self.var_arquivo.get().strip().strip('"')
        if not arquivo or not Path(arquivo).is_file():
            Messagebox.show_error(
                "Escolha um arquivo de modelo existente antes de converter.",
                "Atenção",
                parent=self,
            )
            return

        cli = self.var_cli.get().strip().strip('"')
        config = self.var_config.get().strip().strip('"')
        if not cli or not Path(cli).is_file():
            Messagebox.show_error(
                "Informe o caminho do prusa-slicer-console.exe na aba Configurações.",
                "PrusaSlicer não encontrado",
                parent=self,
            )
            self.abas.select(1)
            return
        if not config or not Path(config).is_file():
            aviso = garantir_config_k7(lambda _mensagem: None)
            detalhe = (
                f"\n\nBaixe um perfil em: {aviso.url}" if aviso else f"\n\nLink: {CONFIG_PAGINA}"
            )
            Messagebox.show_error(
                "O arquivo .ini com os limites da K7 não foi encontrado. "
                "Use o botão 'Baixar perfil' na aba Configurações." + detalhe,
                "Perfil não encontrado",
                parent=self,
            )
            self.abas.select(1)
            return

        pasta = self.var_saida.get().strip().strip('"')
        arquivo_gcode = None
        if pasta:
            arquivo_gcode = Path(pasta) / f"{Path(arquivo).stem}_K7.gcode"

        # Garante que o último tamanho/quantidade escolhidos sobrevivam até mesmo
        # se o programa for fechado de forma inesperada durante a conversão.
        self._salvar_config()

        self.ocupado = True
        self._cancelar_event = threading.Event()
        self.btn_converter.configure(state="disabled")
        self.btn_cancelar.configure(state="normal")
        self.progresso.start(12)
        self.var_status.set("Lendo malha e fatiando...")
        self._escrever_log("info", "--- Iniciando conversão ---")

        threading.Thread(
            target=self._worker,
            args=(
                arquivo,
                self._medida_atual(),
                cli,
                config,
                arquivo_gcode,
                self._cancelar_event,
                self.var_suporte.get(),
                self._quantidade_atual(),
                self.var_suporte_fino.get(),
                self._espessura_suporte_fino(),
            ),
            daemon=True,
        ).start()

    def _cancelar_conversao(self) -> None:
        if self._cancelar_event is not None:
            self._cancelar_event.set()
        self.btn_cancelar.configure(state="disabled")
        self.var_status.set("Cancelando...")
        self._escrever_log("warn", "Cancelamento solicitado; aguardando o fatiador encerrar.")

    def _worker(
        self,
        arquivo: str,
        medida: float,
        cli: str,
        config: str,
        arquivo_gcode: Path | None,
        cancelar: threading.Event,
        tipo_suporte: str,
        quantidade: int,
        suporte_fino: bool,
        suporte_fino_mm: float,
    ) -> None:
        try:
            resultado = converter_3mf_para_k7(
                arquivo,
                medida,
                prusa_cli=cli,
                config_ini=config,
                arquivo_gcode=arquivo_gcode,
                log=self._log,
                cancelar=cancelar.is_set,
                tipo_suporte=tipo_suporte,
                quantidade=quantidade,
                suporte_fino=suporte_fino,
                suporte_fino_mm=suporte_fino_mm,
            )
        except Exception as exc:
            self._enfileirar(("erro", str(exc)))
        else:
            self._enfileirar(("sucesso", resultado))

    def _log(self, nivel: str, mensagem: str) -> None:
        self._enfileirar(("log", nivel, mensagem))

    # ------------------------------------------------------------------
    # Fila de mensagens (thread -> interface)
    # ------------------------------------------------------------------
    def _processar_fila(self) -> None:
        if not self.winfo_exists():
            return

        processados = 0
        try:
            while processados < MAX_ITENS_POR_TICK:
                item = self.fila.get_nowait()
                processados += 1
                tipo = item[0]

                if tipo == "log":
                    self._escrever_log(item[1], item[2])

                elif tipo in ("analise", "analise_erro"):
                    if item[1] != self._analise_geracao:
                        continue  # resultado antigo (slider mudou)
                    self._analise_ocupada = False
                    if tipo == "analise":
                        self._mostrar_analise(item[2])
                    else:
                        self._limpar_previa()
                        self._escrever_log("warn", f"Prévia indisponível: {item[2]}")
                    pendente = self._analise_pendente
                    self._analise_pendente = None
                    if pendente is not None:
                        self._analise_geracao += 1
                        self._analise_ocupada = True
                        threading.Thread(
                            target=self._worker_analise,
                            args=(*pendente, self._analise_geracao),
                            daemon=True,
                        ).start()

                elif tipo == "sucesso":
                    self._finalizar_sucesso(item[1])
                    caminho_gcode = str(item[1].arquivo_gcode)
                    self._tempo_manual = False
                    self.var_gcode_calculo.set(caminho_gcode)
                    # Depois do aviso de conclusão, preenche a aba Calc.
                    self.after(
                        50,
                        lambda caminho=caminho_gcode: self._calcular_orcamento(caminho),
                    )
                    self._dialogo_pendente = (
                        "sucesso",
                        str(item[1].arquivo_gcode),
                        "Conversão concluída",
                    )

                elif tipo == "erro":
                    self._finalizar_erro(item[1])
                    self._dialogo_pendente = ("error", item[1], "Falha na conversão")

        except queue.Empty:
            pass
        except tk.TclError:
            return

        self._mostrar_dialogo_pendente()
        try:
            self._job_fila = self.after(100, self._processar_fila)
        except tk.TclError:
            pass

    def _mostrar_dialogo_pendente(self) -> None:
        """Mostra no máximo um aviso por tick, fora do loop de drenagem."""
        if self._dialogo_pendente is None or not self.winfo_exists():
            return
        tipo, mensagem, titulo = self._dialogo_pendente
        self._dialogo_pendente = None
        try:
            if tipo == "sucesso":
                self._mostrar_dialogo_sucesso(mensagem, titulo)
            elif tipo == "info":
                Messagebox.show_info(mensagem, titulo, parent=self)
            else:
                Messagebox.show_error(mensagem, titulo, parent=self)
        except tk.TclError:
            pass

    def _mostrar_dialogo_sucesso(self, caminho_gcode: str, titulo: str) -> ttk.Toplevel:
        """Diálogo de conclusão com o crédito ao desenvolvedor linkado."""
        dialogo = ttk.Toplevel(self)
        dialogo.title(titulo)
        dialogo.resizable(False, False)
        dialogo.transient(self)

        corpo = ttk.Frame(dialogo, padding=18)
        corpo.pack(fill="both", expand=True)
        ttk.Label(
            corpo,
            text=f"G-code gerado em:\n{caminho_gcode}",
            bootstyle="primary",
            justify="left",
            wraplength=420,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(corpo, text="Desenvolvedor: ", bootstyle="secondary").grid(
            row=1, column=0, sticky="w", pady=(14, 0)
        )
        link = ttk.Label(
            corpo,
            text=NOME_DESENVOLVEDOR,
            bootstyle="primary",
            cursor="hand2",
            font=("Segoe UI", 9, "underline"),
        )
        link.grid(row=1, column=1, sticky="w", pady=(14, 0))
        link.bind("<Button-1>", lambda _evento: self._abrir_url(URL_LINKEDIN))
        ttk.Button(
            corpo, text="OK", bootstyle="success", width=10, command=dialogo.destroy
        ).grid(row=2, column=0, columnspan=2, sticky="e", pady=(16, 0))

        dialogo.update_idletasks()
        x = self.winfo_x() + max((self.winfo_width() - dialogo.winfo_width()) // 2, 0)
        y = self.winfo_y() + max((self.winfo_height() - dialogo.winfo_height()) // 2, 0)
        dialogo.geometry(f"+{x}+{y}")
        dialogo.focus_set()
        return dialogo

    def _finalizar_sucesso(self, resultado: ResultadoConversao) -> None:
        self.ocupado = False
        self._cancelar_event = None
        self.btn_converter.configure(state="normal")
        self.btn_cancelar.configure(state="disabled")
        self.progresso.stop()
        self.resultado = resultado
        self.btn_abrir.configure(state="normal")
        self.btn_pasta.configure(state="normal")
        self.var_status.set(f"Concluído: {resultado.arquivo_gcode.name}")

    def _finalizar_erro(self, mensagem: str) -> None:
        self.ocupado = False
        self._cancelar_event = None
        self.btn_converter.configure(state="normal")
        self.btn_cancelar.configure(state="disabled")
        self.progresso.stop()
        self.var_status.set("Falha na conversão")
        self._escrever_log("error", f"Falha: {mensagem}")

    # ------------------------------------------------------------------
    # Log e utilidades
    # ------------------------------------------------------------------
    def _atualizar_cores_log(self) -> None:
        paleta = {
            "info": "#79c0ff",
            "warn": "#e3b341",
            "error": "#ff7b72",
            "success": "#56d364",
            "debug": "#9da7b3",
            "tempo": "#6e7681",
        }
        for tag, cor in paleta.items():
            self.texto_log.tag_configure(tag, foreground=cor)

    def _escrever_log(self, nivel: str, mensagem: str) -> None:
        if nivel not in {"info", "warn", "error", "success", "debug"}:
            nivel = "info"
        self.texto_log.configure(state="normal")
        self.texto_log.insert("end", f"[{datetime.now():%H:%M:%S}] ", "tempo")
        self.texto_log.insert("end", f"{mensagem}\n", nivel)
        total_linhas = int(self.texto_log.index("end-1c").split(".")[0])
        if total_linhas > MAX_LINHAS_LOG_GUI:
            self.texto_log.delete("1.0", f"{total_linhas - MAX_LINHAS_LOG_GUI}.0")
        self.texto_log.see("end")
        self.texto_log.configure(state="disabled")

    def _limpar_log(self) -> None:
        self.texto_log.configure(state="normal")
        self.texto_log.delete("1.0", "end")
        self.texto_log.configure(state="disabled")

    def _atualizar_previa_saida(self) -> None:
        arquivo = self.var_arquivo.get().strip().strip('"')
        if not arquivo:
            self.var_previa_saida.set("Saída: escolha um arquivo para começar")
            return
        pasta = self.var_saida.get().strip().strip('"')
        destino = Path(pasta) if pasta else Path(arquivo).parent
        gcode = destino / f"{Path(arquivo).stem}_K7.gcode"
        self.var_previa_saida.set(f"Saída: {gcode}")

    def _abrir_no_sistema(self, caminho: Path) -> None:
        if not caminho.exists():
            self._escrever_log("warn", f"Arquivo não encontrado: {caminho}")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(caminho))  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.run(["open", str(caminho)], check=False)
            else:
                subprocess.run(["xdg-open", str(caminho)], check=False)
        except OSError as exc:
            self._escrever_log("error", f"Não foi possível abrir '{caminho}': {exc}")

    def _abrir_gcode(self) -> None:
        if self.resultado is not None:
            self._abrir_no_sistema(self.resultado.arquivo_gcode)

    def _abrir_pasta(self) -> None:
        if self.resultado is not None:
            self._abrir_no_sistema(self.resultado.arquivo_gcode.parent)


def _executar_preflight() -> None:
    """Mostra uma tela curta enquanto verifica/instala o PrusaSlicer."""
    splash = tk.Tk()
    splash.title("Conversor K7 - preparando o ambiente")
    splash.geometry("470x155")
    splash.resizable(False, False)
    splash.eval("tk::PlaceWindow . center")

    mensagem = tk.StringVar(value="Verificando dependências...")
    percentual = tk.IntVar(value=0)

    quadro = tk_ttk.Frame(splash, padding=16)
    quadro.pack(fill="both", expand=True)
    tk_ttk.Label(
        quadro, text="Preparando o Conversor K7", font=("Segoe UI", 12, "bold")
    ).pack(anchor="w")
    tk_ttk.Label(quadro, textvariable=mensagem, wraplength=430).pack(
        anchor="w", pady=(10, 0)
    )
    tk_ttk.Progressbar(quadro, variable=percentual, maximum=100).pack(
        fill="x", pady=(12, 0)
    )

    def log(texto: str) -> None:
        mensagem.set(texto)
        splash.update_idletasks()
        splash.update()

    def progresso(valor: int) -> None:
        percentual.set(valor)
        splash.update_idletasks()
        splash.update()

    ultimo_tick = [0.0]

    def tick() -> None:
        # Durante a instalação silenciosa não há progresso: este callback
        # mantém a janela respondendo (arrastar/fechar) sem gastar CPU à toa.
        agora = time.monotonic()
        if agora - ultimo_tick[0] < 0.1:
            return
        ultimo_tick[0] = agora
        try:
            splash.update()
        except tk.TclError:
            pass

    try:
        avisos = verificar_e_instalar(log, progresso, tick)
    finally:
        splash.destroy()

    for aviso in avisos:
        tk_messagebox.showwarning(
            "Dependência pendente",
            f"{aviso.nome}\n\n{aviso.motivo}\n\n"
            f"Baixe em: {aviso.url}\n\n"
            "O programa será aberto, mas a conversão só funcionará depois "
            "de instalar esse item.",
        )


def main() -> None:
    # Cria/copia o perfil antes de qualquer janela, inclusive no selftest.
    garantir_config_k7(lambda _mensagem: None)

    if "--selftest" in sys.argv:
        janela = AppK7()
        janela.withdraw()
        janela.update_idletasks()
        janela.update()
        janela.destroy()
        if getattr(sys, "frozen", False):
            marcador = Path(sys.executable).with_name("selftest_ok.txt")
            marcador.write_text(
                f"GUI criada com sucesso.\nPerfil: {CONFIG_PADRAO}\n"
                f"Perfil encontrado: {CONFIG_PADRAO.is_file()}\n",
                encoding="utf-8",
            )
        return

    if os.environ.get("K7_SKIP_PREFLIGHT") != "1":
        _executar_preflight()

    AppK7().mainloop()


if __name__ == "__main__":
    main()
