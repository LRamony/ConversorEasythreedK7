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
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog
from tkinter import messagebox as tk_messagebox
from tkinter import ttk as tk_ttk

import ttkbootstrap as ttk
from ttkbootstrap.dialogs import Messagebox

from bootstrap import CONFIG_PAGINA, garantir_config_k7, verificar_e_instalar
from converter_k7 import (
    ALTURA_MESA_MM,
    CONFIG_PADRAO,
    LARGURA_MESA_MM,
    LIMITE_SEGURANCA_MM,
    PROFUNDIDADE_MESA_MM,
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
            size=(1000, 950),
            minsize=(900, 820),
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
        self._analise_pendente: tuple[str, float] | None = None

        self.var_arquivo = tk.StringVar(value="")
        self.var_formato = tk.StringVar(value="3MF e STL")
        self.var_tamanho = tk.DoubleVar(value=60.0)
        self.var_scale = tk.DoubleVar(value=60.0)
        self.var_saida = tk.StringVar(value="")
        self.var_previa_saida = tk.StringVar(value="Saída: escolha um arquivo para começar")
        self.var_cli = tk.StringVar(value=str(localizar_prusa_cli() or ""))
        self.var_config = tk.StringVar(value=str(CONFIG_PADRAO))
        self.var_status = tk.StringVar(value="Pronto para converter.")

        self._construir_interface()
        self._aplicar_config_salva()
        self.protocol("WM_DELETE_WINDOW", self._ao_fechar)
        self.after(100, self._processar_fila)

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
        abas.add(aba_conversao, text="  Converter  ")
        abas.add(aba_config, text="  Configurações  ")

        self._construir_aba_conversao(aba_conversao)
        self._construir_aba_config(aba_config)
        self._construir_log(principal)
        self._construir_acoes(principal)

        ttk.Label(
            principal,
            textvariable=self.var_status,
            bootstyle="secondary",
            anchor="w",
        ).grid(row=4, column=0, sticky="ew", pady=(6, 0))

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
        escala.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(10, 2))
        ttk.Label(
            cartao_tamanho,
            text=f"Trava de segurança: qualquer valor acima de {LIMITE_SEGURANCA_MM:g} mm "
            "é limitado automaticamente antes de fatiar.",
            bootstyle="warning",
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))

        cartao_saida = self._cartao(aba, "3. Arquivo de saída")
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
        ttk.Button(
            cartao_previa,
            text="Calcular prévia",
            bootstyle="secondary-outline",
            command=self._agendar_analise,
        ).grid(row=0, column=1, rowspan=3, sticky="e", padx=(10, 0))

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
            "Mesa: 0 °C  |  Camada: 0,2 mm  |  Preenchimento: 15%  |  Suportes automáticos: ligados",
            bootstyle="secondary",
            justify="left",
        ).pack(anchor="w")

    def _construir_log(self, principal: ttk.Frame) -> None:
        quadro = ttk.Labelframe(principal, text="  Log de execução  ", padding=6, bootstyle="info")
        quadro.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        quadro.columnconfigure(0, weight=1)
        quadro.rowconfigure(0, weight=1)

        self.texto_log = ttk.ScrolledText(quadro, height=9, wrap="word", bootstyle="default")
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

        # Uma única thread de prévia por vez: arrastar o slider não pode criar
        # dezenas de leituras simultâneas da mesma malha.
        if self._analise_ocupada:
            self._analise_pendente = (arquivo, self._medida_atual())
            return

        self.lbl_dimensoes.configure(text="Dimensões atuais: calculando...")
        self._analise_ocupada = True
        threading.Thread(
            target=self._worker_analise,
            args=(arquivo, self._medida_atual(), self._analise_geracao),
            daemon=True,
        ).start()

    def _worker_analise(self, arquivo: str, medida: float, geracao: int) -> None:
        try:
            analise = analisar_arquivo(arquivo, medida)
        except Exception as exc:  # a prévia nunca deve derrubar a interface
            self._enfileirar(("analise_erro", geracao, str(exc)))
        else:
            self._enfileirar(("analise", geracao, analise))

    def _mostrar_analise(self, analise: AnaliseMalha) -> None:
        self.lbl_dimensoes.configure(
            text=f"Dimensões atuais: {_fmt(analise.dimensoes_originais)} mm"
        )
        self.lbl_escala.configure(
            text=f"Fator de escala: {analise.fator_escala:.4f}x  "
            f"({analise.partes} parte(s))"
        )
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
                    self._dialogo_pendente = (
                        "info",
                        f"G-code gerado em:\n{item[1].arquivo_gcode}",
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
            self.after(100, self._processar_fila)
        except tk.TclError:
            pass

    def _mostrar_dialogo_pendente(self) -> None:
        """Mostra no máximo um aviso por tick, fora do loop de drenagem."""
        if self._dialogo_pendente is None or not self.winfo_exists():
            return
        tipo, mensagem, titulo = self._dialogo_pendente
        self._dialogo_pendente = None
        try:
            if tipo == "info":
                Messagebox.show_info(mensagem, titulo, parent=self)
            else:
                Messagebox.show_error(mensagem, titulo, parent=self)
        except tk.TclError:
            pass

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
