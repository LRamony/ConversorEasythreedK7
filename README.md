# Conversor 3MF / STL → G-code (Easythreed K7)

Interface gráfica feita com **ttkbootstrap** sobre o tkinter. Ela:

1. Lê um arquivo `.3mf` (ou `.stl`) com `trimesh`;
2. Calcula as dimensões atuais e aplica uma **escala proporcional** para caber na mesa;
3. Centraliza a peça em **X=50, Y=50** e encosta a base em **Z=0**;
4. Exporta um STL temporário já ajustado;
5. Chama o **PrusaSlicer em modo console** com o perfil `k7_config.ini` e gera o `.gcode`;
6. Apaga o STL temporário ao final.

> Trava de segurança: nenhuma medida acima de **95 mm** é aplicada, mesmo que você digite um valor maior.

## Estrutura

| Arquivo | Função |
|---|---|
| `app.py` | Interface gráfica (ttkbootstrap) |
| `converter_k7.py` | Núcleo de conversão, reutilizável sem GUI |
| `bootstrap.py` | Verificação inicial: detecta/instala o PrusaSlicer e gera avisos com links |
| `k7_config.ini` | Perfil da K7 (mesa 100³, bico 0,4, 195/200 °C, mesa 0 °C, suportes automáticos) |
| `test_pipeline.py` | Testes do pipeline e smoke test da interface |
| `requirements.txt` | Dependências |
| `k7_gui_settings.json` | Preferências salvas automaticamente (caminhos e filtro de arquivo) |

> A interface usa **apenas o tema `bootstrap-dark`** (padrão fixo, sem seletor de temas).

## Instalação (venv recomendado)

No PowerShell, na pasta do projeto:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Como usar

```powershell
.\.venv\Scripts\python.exe app.py
```

Na aba **Converter**:

1. Em **Arquivo de entrada**, escolha o filtro (`3MF e STL`, `Somente 3MF` ou `Somente STL`) e clique em **Procurar...**;
2. Defina a **medida máxima** (campo numérico ou slider). A prévia mostra dimensões originais, fator de escala e dimensões finais;
3. Opcionalmente escolha a **pasta de saída** (em branco = mesma pasta do modelo). O nome gerado é `<nome>_K7.gcode`;
4. Clique em **Converter para G-code**.

### Verificação automática ao iniciar

Ao abrir o `.exe`, uma tela rápida verifica se o `prusa-slicer-console.exe` existe. Se não existir, o programa:

1. consulta o release mais recente no GitHub;
2. baixa o `PrusaSlicer-*-setup.exe` oficial (com validação SHA-256);
3. instala em modo silencioso (`/VERYSILENT /SUPPRESSMSGBOXES /NORESTART`).

Se o download/installação falhar (por exemplo, sem internet ou sem permissão de administrador), aparece um aviso com o motivo e o link oficial de download. O programa ainda abre, mas a conversão só funcionará após instalar manualmente. As dependências Python já vêm embutidas no executável.

### Perfil `k7_config.ini`

O `.ini` vem embutido no `.exe`, mas a pasta temporária `_MEIxxxx` onde ele é extraído **muda a cada execução** e é apagada ao fechar. Por isso o programa copia o perfil para um caminho estável:

```
%LOCALAPPDATA%\ConversorK7\k7_config.ini
```

Se esse arquivo faltar, ele é copiado do arquivo embutido; se o embutido também faltar, o perfil é recriado automaticamente com os limites da K7. Não é mais possível ficar com um caminho `_MEI` inválido salvo nas preferências.

Na aba **Configurações**, informe:

- o caminho do `prusa-slicer-console.exe` (botão **Detectar automaticamente** costuma encontrar);
- o caminho do `k7_config.ini` (o botão **Baixar perfil** salva uma cópia do perfil onde você quiser, e o link ao lado indica onde obter perfis alternativos).

O botão **Abrir G-code** abre o arquivo gerado no programa padrão do sistema.

## Uso por linha de comando / código

```powershell
.\.venv\Scripts\python.exe converter_k7.py
```

Ou importando:

```python
from converter_k7 import converter_3mf_para_k7, converter_stl_para_k7

converter_3mf_para_k7("peca.3mf", 60)
converter_stl_para_k7("modelo.stl", 60)  # mesmo pipeline
```

## Testes

```powershell
.\.venv\Scripts\python.exe test_pipeline.py           # núcleo
.\.venv\Scripts\python.exe test_pipeline.py --gui     # núcleo + janela
```

Os testes substituem o `subprocess.run` por uma função falsa, então **não é preciso ter o PrusaSlicer instalado** para validar escala, centralização, trava de 95 mm, limpeza do STL temporário e a criação da interface.

## Instalando o PrusaSlicer

Baixe o instalador em <https://www.prusaslicer.org/> e use o executável de console:

```
C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer-console.exe
```

Antes de imprimir de verdade, confira no próprio PrusaSlicer se o perfil corresponde à sua K7 ( firmware, origem do G-code e offsets do bico ).
