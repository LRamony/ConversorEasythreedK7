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
| `calculo.py` | Leitura do G-code e cálculo de custo/preço da aba Calc |
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
3. Em **Tipo de suporte**, escolha entre **sem suporte**, as variantes **normais** (grade, linhas, quadrados, colmeia, snug) ou as variantes **árvore/orgânicas** (padrão, reforçada, econômica);
4. Opcionalmente escolha a **pasta de saída** (em branco = mesma pasta do modelo). O nome gerado é `<nome>_K7.gcode`;
5. Clique em **Converter para G-code**.

### Tipo de suporte

O seletor da aba **Converter** escolhe o estilo de suporte. O programa **não altera** o seu `k7_config.ini`: ele copia o perfil para um arquivo temporário, aplica as chaves do PrusaSlicer (`support_material_style`, `support_material_angling`, etc.) e apaga esse arquivo ao final da conversão.

- **Normal**: grade, linhas, quadrados, colmeia ou ajustado (snug);
- **Árvore (orgânico)**: padrão, reforçado (mais vertical/mais colunas) ou econômico (mais inclinado, menos material).

### Aba Calc — quanto cobrar pela impressão

A aba **Calc** lê o início e o rodapé do G-code gerado pelo PrusaSlicer (tempo estimado e filamento usado) e calcula o preço. O PrusaSlicer grava essas estatísticas no fim do arquivo, junto com a lista de parâmetros. Você informa a **margem de lucro (%)**; os custos já vêm com valores padrão editáveis:

| Custo | Padrão |
|---|---|
| Filamento | R$ 100,00/kg |
| Energia elétrica | R$ 0,92/kWh × 60 W |
| Desgaste/manutenção | R$ 1,50/h |
| Mão de obra | R$ 15,00/h |
| Acabamento | R$ 2,00/peça |
| Embalagem | R$ 1,50/peça |

Após cada conversão bem-sucedida o cálculo é feito automaticamente com o G-code recém-gerado, sem botão. Para analisar um arquivo antigo, use **Procurar...**. Qualquer alteração em margem, tempo ou custos recalcula na hora. O resultado mostra cada custo, o custo total, o **preço final** com a margem e o lucro. Se o G-code não informar o peso do filamento, ele é estimado a partir dos mm usados (PLA 1,75 mm).

O campo **Tempo de impressão (h)** mostra o tempo lido do G-code. Se o arquivo não informar o tempo, ele usa **1,5 h** por padrão e pode ser editado: o valor digitado passa a ter prioridade sobre o do G-code até você escolher outro arquivo. A prévia da peça também é automática (ao escolher o arquivo, apertar Enter ou mover o slider de escala), por isso não há mais botão "Calcular prévia".

Se as linhas `; estimated printing time ...` e `; filament used ...` não existirem, ative **Verbose G-code** no PrusaSlicer em *Configurações de impressão > Opções de saída* e fatie novamente — ou apenas informe o tempo no campo editável. A aba também aceita os formatos `HH:MM:SS`, `2h 30m`, `1d 2h 3m 4s` e segundos puros (`;TIME:38846`).

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

## Proteções contra travamento

Depois de uma falha ao gerar G-code de um `.3mf` (fatiador preso esperando uma janela de erro), o programa e até o PC podiam travar. As causas encontradas e as correções aplicadas:

| Causa do travamento | Correção |
|---|---|
| `subprocess.run` sem timeout: se o fatiador abrisse um diálogo modal ou ficasse preso, a thread esperava para sempre | Execução com `Popen` + `communicate` em fatias de 0,25 s, **prazo de 5 min** e `kill()` automático ao estourar o tempo |
| Saída do fatiador sem limite podia encher a RAM (milhares de linhas de log) | Saída limitada às últimas 200 linhas |
| Arquivos gigantes/malhas com milhões de triângulos esgotavam CPU e memória ao carregar | Limite de **200 MB** por arquivo e **2.000.000** de triângulos, recusados antes do processamento |
| Fila de mensagens da interface crescia sem fim e travava o tkinter | Fila limitada a 500 itens (descarta os mais antigos), no máximo 150 itens processados por ciclo e log visual limitado a 500 linhas |
| Diálogos de erro abertos **dentro** do loop da fila bloqueavam a janela | Avisos são enfileirados e exibidos no máximo um por ciclo, fora do loop |
| Arrastar o slider da prévia criava várias threads lendo a mesma malha | Uma única thread de prévia por vez; resultados antigos são descartados |
| Download/instalação do preflight bloqueavam a tela por até 15 min | Download com prazo (10 min) e limite (400 MB), instalação com prazo (10 min), `kill()` no timeout e splash atualizado por um callback `tick` |
| Não havia como interromper uma conversão já iniciada | Botão **Cancelar** encerra o fatiador; fechar a janela também cancela |

Na prática: uma conversão problemática agora termina com uma mensagem clara (ou é cancelada), em vez de deixar o programa sem responder.

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

Os testes substituem a execução do fatiador (`_executar_slicer`) por uma função falsa, então **não é preciso ter o PrusaSlicer instalado** para validar escala, centralização, trava de 95 mm, timeout, cancelamento, limites de arquivo e a criação da interface.

## Gerando o `.exe`

Com o venv ativado (ou chamando o Python dele), execute na pasta do projeto:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed --name ConversorK7 --icon icon.ico --add-data "k7_config.ini;." --collect-all trimesh --collect-all ttkbootstrap --collect-all networkx --hidden-import=lxml app.py
```

Para forçar uma reconstrução completa (limpa o cache do PyInstaller):

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed --clean --name ConversorK7 --icon icon.ico --add-data "k7_config.ini;." --collect-all trimesh --collect-all ttkbootstrap --collect-all networkx --hidden-import=lxml app.py
```

Se der `PermissionError: [WinError 5]`, é porque uma instância antiga está usando o arquivo. Feche o programa (ou encerre o processo) e rode o comando novamente:

```powershell
Get-Process -Name ConversorK7 -ErrorAction SilentlyContinue | Stop-Process -Force
```

### Onde o executável fica

Depois que o build terminar com `Build complete!`, o arquivo estará em:

```
ConversorEasythreedK7\dist\ConversorK7.exe
```

Ou, de forma relativa à pasta do projeto: `dist\ConversorK7.exe`. Ele é **one-file**: pode ser copiado sozinho para outra máquina, sem precisar levar o `k7_config.ini` junto (ele já vem embutido).

Para conferir rapidamente se o `.exe` abriu e encontrou o perfil:

```powershell
& ".\dist\ConversorK7.exe" --selftest
Get-Content ".\dist\selftest_ok.txt"
```

## Instalando o PrusaSlicer

Baixe o instalador em <https://www.prusaslicer.org/> e use o executável de console:

```
C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer-console.exe
```

Antes de imprimir de verdade, confira no próprio PrusaSlicer se o perfil corresponde à sua K7 ( firmware, origem do G-code e offsets do bico ).
