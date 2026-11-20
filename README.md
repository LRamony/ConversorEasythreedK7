# Conversor 3MF / STL → G-code (Easythreed K7)

Interface gráfica feita com **ttkbootstrap** sobre o tkinter. Ela:

1. Lê um arquivo `.3mf` (ou `.stl`) com `trimesh`;
2. Calcula as dimensões atuais e aplica uma **escala proporcional** para caber na mesa;
3. Repete a peça quantas vezes você pedir, organizando tudo em grade e reduzindo a escala automaticamente;
4. Centraliza o conjunto em **X=50, Y=50** e encosta a base em **Z=0**;
5. Exporta um STL temporário já ajustado;
6. Chama o **PrusaSlicer em modo console** com o perfil `k7_config.ini` e gera o `.gcode`;
7. Apaga o STL temporário ao final.

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
| `k7_gui_settings.json` | Preferências salvas automaticamente (caminhos, último tamanho, quantidade, ajustes de impressão, aderência, diâmetro do suporte e simplificação automática) |

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

1. Em **Arquivo de entrada**, clique em **Procurar...**. A própria janela de arquivos oferece os filtros de `.3mf` e `.stl`;
2. Defina a **medida máxima** (apenas campo numérico) e a **quantidade de peças**. A prévia mostra dimensões originais, fator de escala, dimensões do conjunto e o tamanho de cada peça;
3. Em **Tipo de suporte**, escolha entre **sem suporte**, as variantes **normais** (grade, linhas, quadrados, colmeia, snug) ou as variantes **árvore/orgânicas** (padrão, reforçada, econômica). À direita do seletor, marque **Ajustar diâmetro do suporte** e informe o **diâmetro em mm** (padrão `0,9`);
4. Opcionalmente escolha a **pasta de saída** (em branco = mesma pasta do modelo). O nome gerado é `<nome>_K7.gcode`;
5. Clique em **Converter para G-code**.

O **último tamanho máximo escolhido**, a quantidade de peças, os **ajustes de impressão**, a aderência, a **simplificação automática** e o **diâmetro do suporte** voltam como padrão na próxima execução. Os ajustes de impressão são salvos imediatamente quando você altera um campo; os demais são salvos ao converter ou fechar. O botão **Reset** da aba **Ajustes de impressão** restaura preenchimento, velocidades, temperaturas, aderência à mesa e desliga a simplificação automática. O **Reset** ao lado do campo de suporte restaura apenas o suporte (opção desligada e `0,9 mm` de diâmetro).

### Ajustes de impressão

A barra deslizante e as siglas foram removidas. As opções ficam diretamente na aba **Ajustes de impressão**, logo após **Converter**. Lá cada opção aparece com nome completo, unidade, limite e uma explicação do efeito na impressão.

| Nome exibido | Chave do PrusaSlicer | Limite | O que controla |
| --- | --- | --- | --- |
| Preenchimento | `fill_density` | `5` a `100 %` | Quantidade de plástico no interior da peça. Valores baixos deixam a peça mais oca e rápida; valores altos aumentam resistência, material e tempo. |
| Velocidade do perímetro | `perimeter_speed` | `10` a `60 mm/s` | Velocidade das paredes externas. Muito alta pode causar tremida e perder definição nos detalhes. |
| Velocidade do preenchimento | `infill_speed` | `10` a `80 mm/s` | Velocidade das linhas internas. Pode ser maior que a do perímetro, mas em peças pequenas pode não dar tempo de resfriar. |
| Velocidade de viagem | `travel_speed` | `20` a `120 mm/s` | Movimento do bico sem extrudar. Excesso pode causar perda de passo dos motores e deslocar a peça. |
| Temperatura do bico | `temperature` | `180` a `230 °C` | Temperatura do hotend. PLA costuma funcionar entre `190 °C` e `210 °C`. |
| Temperatura do bico na 1ª camada | `first_layer_temperature` | `180` a `230 °C` | Usada somente na primeira camada. Um pouco mais alta ajuda na aderência à mesa. |
| Temperatura da mesa | `bed_temperature` | `0` a `80 °C` | A K7 comum usa `0 °C`; aumente apenas se o material ou a mesa aquecida pedirem. |
| Temperatura da mesa na 1ª camada | `first_layer_bed_temperature` | `0` a `80 °C` | Ajuda na aderência da primeira camada. Mantenha `0 °C` sem mesa aquecida. |

Os campos começam com os valores do `k7_config.ini` escolhido e o que você alterar tem prioridade na próxima execução. Cada alteração é salva na hora e aparece imediatamente no painel **Limites aplicados no G-code** da aba **Configurações**; o botão **Reset** à direita do texto “ao final da conversão” restaura todos os campos aos valores padrão. Nesse painel, bico, filamento e altura de camada permanecem fixos, vindos do perfil da K7. Qualquer valor digitado abaixo do mínimo ou acima do máximo é automaticamente limitado ao intervalo. Assim como o suporte, nada é gravado no seu perfil: os valores são aplicados em um `.ini` temporário que é apagado ao final da conversão.

### Aderência à mesa de impressão

Na aba **Ajustes de impressão**, o seletor **Aderência à mesa de impressão** oferece `None`, `Skirt`, `Brim` e `Raft`. O padrão é `Skirt`, o mesmo do PrusaSlicer; a escolha é salva imediatamente e também volta ao padrão pelo botão **Reset**.

| Opção | Chaves aplicadas no `.ini` temporário | Efeito |
| --- | --- | --- |
| `None` | `skirts = 0`, `brim_width = 0`, `raft_layers = 0` | Sem contorno ou base extra. |
| `Skirt` | `skirts = 1`, `brim_width = 0`, `raft_layers = 0` | Uma volta ao redor da peça para iniciar o fluxo. |
| `Brim` | `skirts = 0`, `brim_width = 6`, `brim_type = outer_only`, `raft_layers = 0` | Borda de `6 mm` na primeira camada, aumentando a fixação. |
| `Raft` | `skirts = 0`, `brim_width = 0`, `raft_layers = 2` | Base sob a peça; mais firme, porém gasta mais material e tempo. |

Assim como os demais ajustes, nada é gravado no seu perfil: as chaves são aplicadas somente no `.ini` temporário e apagadas ao final da conversão.

### Simplificação automática de modelos pesados

Na aba **Ajustes de impressão**, o cartão **Simplificação de modelos pesados** oferece a opção **Simplificar automaticamente acima de 2.000.000 triângulos** (desligada por padrão). A contagem já inclui todas as peças pedidas: com 2 cópias de um modelo de 1.983.190 triângulos, o total é 3.966.380 e o limite é excedido.

- **Desligada:** a conversão é recusada antes de carregar a malha, com orientações para reduzir a quantidade ou simplificar o arquivo (comportamento original).
- **Ligada:** a malha é decimada automaticamente até caber no limite — por exemplo, com 2 cópias o alvo é 1.000.000 triângulos por cópia. O aviso aparece no log e na prévia com o antes/depois e o percentual removido.

O aviso de qualidade é proporcional à redução: abaixo de 20% ele informa que a perda de detalhes deve ser pequena; a partir de 20% ele alerta que detalhes finos podem ser perdidos e que vale conferir a peça antes de imprimir. A simplificação usa o backend `fast-simplification` (já incluído no `.exe`), atua na malha original antes de criar as cópias e não altera seu `k7_config.ini`. A escolha é salva imediatamente e volta ao padrão pelo botão **Reset**.

> Simplificar resolve o travamento, não cria detalhes: se o modelo vier de escaneamento com milhões de triângulos, ainda é melhor exportar uma versão com menos resolução quando possível.

### Tipo de suporte

O seletor da aba **Converter** escolhe o estilo de suporte. O programa **não altera** o seu `k7_config.ini`: ele copia o perfil para um arquivo temporário, aplica as chaves corretas do PrusaSlicer (`support_material_style`, `support_tree_angle`, `support_tree_angle_slow`, `fill_density`, `perimeter_speed`, `temperature`, etc.) e apaga esse arquivo ao final da conversão.

- **Normal**: grade, linhas, quadrados, colmeia ou ajustado (snug);
- **Árvore (orgânico)**: padrão (`40°`/`25°`), reforçado (`30°`/`20°`, mais vertical) ou econômico (`55°`/`35°` e densidade de ponta `5%`).

> Correção: a versão anterior usava `support_material_angling`, que não é uma chave reconhecida pelo PrusaSlicer e era ignorada. Agora o ângulo é aplicado de verdade com `support_tree_angle` e `support_tree_angle_slow`.

### Ajustar diâmetro do suporte

Ao marcar **Ajustar diâmetro do suporte**, o valor informado é o **diâmetro do ramo/árvore** (`0,6` a `3,0 mm`, padrão `0,9 mm`). A largura de extrusão é derivada automaticamente desse diâmetro (≈ `diâmetro / 3`, limitada a `0,22–0,50 mm` para respeitar o bico de `0,4 mm` e as camadas de `0,20 mm`).

Por exemplo, com `1,5 mm`: extrusão `0,50 mm`, ramo `1,5 mm`, ponta `0,75 mm`. Com `2,5 mm`: ramo `2,5 mm`, ponta `1,25 mm` e extrusão `0,50 mm` — ou seja, aumentar o valor engrossa a árvore. A extrusão para de crescer em `0,50 mm`, pois é o limite seguro para o bico de `0,4 mm` e a camada de `0,20 mm`.

O que muda no `.ini` temporário:

- `support_tree_branch_diameter` = valor escolhido e `support_tree_tip_diameter` = 50% dele;
- `support_material_extrusion_width` e `support_material_interface_extrusion_width` = extrusão derivada;
- `support_material_with_sheath = 0` (sem bainha extra);
- `support_tree_branch_diameter_angle = 0` — **o ponto principal**: o tronco não engrossa progressivamente até a base como no padrão `5°`;
- `support_tree_branch_diameter_double_wall = 0` — desativa paredes duplas;
- `support_tree_branch_distance = 2` e `support_tree_top_rate = 5%` — menos pontos de contato com a peça, mais fácil de soltar e menos material.

Vale para suporte normal e árvore, não altera o seu perfil e não tem efeito com **Sem suporte**. O último valor é lembrado; **Reset** volta para `0,90 mm` com a opção desligada.

> Se você usava a versão anterior, valores salvos até `0,50 mm` eram larguras de extrusão. Eles são convertidos automaticamente para diâmetro multiplicando por 3 (por exemplo, `0,30` vira `0,90`). Valores acima de `0,50 mm` já eram tratados como diâmetro e permanecem intactos.

### Várias peças na mesma mesa

O campo **Quantidade de peças** (de 1 a 20) distribui as cópias em uma grade com 2 mm de espaçamento. O programa testa várias combinações de colunas/linhas e escolhe a que permite o **maior tamanho possível** sem passar dos 95 mm úteis; por isso, ao aumentar a quantidade, cada peça sai menor automaticamente. A prévia mostra o conjunto (`Conjunto 2x2: ...`), o tamanho individual (`Cada peça: ...`) e, na coluna à direita do texto, **Largura (X)**, **Comprimento (Y)** e **Altura (Z)** de **uma única peça já escalada** — sem contar o espaçamento da grade. Se o total de triângulos passar de 2.000.000, a conversão é recusada com uma mensagem explicando como reduzir a quantidade — a menos que você tenha ativado **Simplificar automaticamente acima de 2.000.000 triângulos** na aba **Ajustes de impressão**. Nesse caso a malha é reduzida até o limite e a prévia/log mostram quantos triângulos foram removidos e se a perda de detalhes pode ser perceptível.

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

Após cada conversão bem-sucedida o cálculo é feito automaticamente com o G-code recém-gerado, sem botão. Para analisar um arquivo antigo, use **Procurar...** ou digite/cole o caminho direto no campo **G-code**. Qualquer alteração no G-code, na margem, no tempo ou nos custos recalcula na hora; se o caminho apontar para um arquivo que não existe mais, o resultado antigo é limpo. O resultado mostra cada custo, o custo total, o **preço final** com a margem e o lucro, e o botão **Copiar resultado** (no canto superior direito do resultado) copia tudo para a área de transferência — ele só aparece quando há um resultado. Se o G-code não informar o peso do filamento, ele é estimado a partir dos mm usados (PLA 1,75 mm).

O campo **Tempo de impressão (h)** mostra o tempo lido do G-code. Se o arquivo não informar o tempo, ele usa **1,5 h** por padrão e pode ser editado: o valor digitado passa a ter prioridade sobre o do G-code até você escolher outro arquivo. A prévia da peça também é automática (ao escolher o arquivo, apertar Enter, mudar a medida máxima ou a quantidade), por isso não há mais botão "Calcular prévia".

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
| Arquivos gigantes/malhas com milhões de triângulos esgotavam CPU e memória ao carregar | Limite de **200 MB** por arquivo e **2.000.000** de triângulos (a contagem já inclui todas as cópias pedidas). Acima disso, a conversão é recusada antes do processamento, a menos que a **simplificação automática** esteja ativada — nela a malha é decimada até o limite com aviso de perda de qualidade |
| Muitas cópias podiam gerar uma malha enorme ou pedidos absurdos | Quantidade limitada a **20 peças**, com aviso e redução automática; o escalonamento é escolhido para caber na mesa |
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

# Reduz malhas acima de 2.000.000 triângulos em vez de recusá-las
converter_3mf_para_k7("peca.3mf", 60, quantidade=2, simplificar_pesado=True)
```

## Testes

```powershell
.\.venv\Scripts\python.exe test_pipeline.py           # núcleo
.\.venv\Scripts\python.exe test_pipeline.py --gui     # núcleo + janela
```

Os testes substituem a execução do fatiador (`_executar_slicer`) por uma função falsa, então **não é preciso ter o PrusaSlicer instalado** para validar escala, centralização, trava de 95 mm, timeout, cancelamento, limites de arquivo e a criação da interface.

## Links e créditos

- **Documentação**: link no canto inferior direito da janela → [README no GitHub](https://github.com/LRamony/ConversorEasythreedK7/blob/main/README.md);
- **Desenvolvedor**: o aviso “Conversão concluída” exibe `Desenvolvedor: Lucas Ramony`, com o nome linkado ao [LinkedIn](https://www.linkedin.com/in/ramony-sabino-5a896536/);

## Gerando o `.exe`

Com o venv ativado (ou chamando o Python dele), execute na pasta do projeto:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed --name ConversorK7 --icon icon.ico --add-data "k7_config.ini;." --collect-all trimesh --collect-all fast_simplification --collect-all ttkbootstrap --collect-all networkx --hidden-import=lxml app.py
```

Para forçar uma reconstrução completa (limpa o cache do PyInstaller):

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed --clean --name ConversorK7 --icon icon.ico --add-data "k7_config.ini;." --collect-all trimesh --collect-all fast_simplification --collect-all ttkbootstrap --collect-all networkx --hidden-import=lxml app.py
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
