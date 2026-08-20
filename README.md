# YellowTrap Pipeline

Processamento automatizado de imagens de armadilhas adesivas — **amarelas e
azuis** — usadas na captura e identificação de pragas na lavoura: tripes,
afídeos, cigarrinhas, mosca branca.

**VARD** · FATEC POMPEIA SHUNJI NISHIMURA ( POMPEIA/SP)

O sistema recebe as fotos individuais dos quadrantes da placa (capturadas por
câmera/microscópio), **renomeia** segundo o esquema escolhido e **recorta** o
quadrante central de cada uma. A entrega final são os quadrantes limpos — a
identificação das pragas por *deep learning* é uma etapa posterior, fora do
escopo deste repositório.

### Uma única linha de comando para as duas cores

O detector de grade **não olha a cor do papel, olha a geometria**. Amarela e
azul passam pelo mesmo comando, sem flag nenhuma:

```powershell
python scripts/run_pipeline.py --modo sequencial --entrada "D:\lote_amarelas"
python scripts/run_pipeline.py --modo sequencial --entrada "D:\lote_azuis"
```

Existe um `--perfil {auto,amarela,azul}` opcional, que **não muda o algoritmo**:
ele só aperta a faixa de largura aceita para o quadrante, como trava extra num
lote difícil. O default `auto` atende as duas.

Medido sobre o acervo real (65 fotos azuis + 40 amarelas), com o critério que
importa para a etapa seguinte — *nenhuma linha da grade dentro do quadrante
entregue*:

| | Versão anterior | Esta versão |
|---|---|---|
| Azuis com recorte limpo | 7 / 65 | **65 / 65** |
| Amarelas com recorte limpo | 35 / 40 | **40 / 40** |
| Dispersão da largura do quadrante (azul) | ±0,152 | **±0,010** |
| Dispersão da largura do quadrante (amarela) | ±0,051 | **±0,009** |

`tests/test_recorte_acervo.py` trava esses números contra as fotos reais.

---

## Índice

- [O pipeline em 2 etapas](#o-pipeline-em-2-etapas)
- [Modos de operação](#modos-de-operação)
- [Requisitos](#requisitos)
- [Setup](#setup)
- [Comandos rápidos](#comandos-rápidos)
- [Como usar](#como-usar)
- [Usando como biblioteca](#usando-como-biblioteca)
- [Escala: milhares de fotos](#escala-milhares-de-fotos)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Configuração](#configuração)
- [Como interpretar os logs](#como-interpretar-os-logs)
- [Tratamento de falhas](#tratamento-de-falhas)
- [Testes](#testes)
- [Preservação de qualidade](#preservação-de-qualidade)
- [Troubleshooting](#troubleshooting)
- [Integração com n8n / Railway](#integração-com-n8n--railway)
- [Mudanças recentes](#mudanças-recentes)

---

## O pipeline em 2 etapas

```
data/01_entrada_bruta/     DSC0001.JPG ... DSC0040.JPG   (nomes da câmera)
        │
        │  Etapa 1 — nomeação: monta o de-para (a1..d10 ou VARD1, VARD2...)
        │            SEM escrever nada em disco
        │
        │  Etapa 2 — recorte do quadrante central, nos 4 lados
        │            (PARALELO, N processos)
        │            grava UM arquivo por foto, já com o nome final
        ▼
data/03_recortadas/        a1.png … d10.png   ou   VARD1.png, VARD2.png …
        │
        └─ data/_relatorios/<lote_id>/sumario.json

data/02_renomeadas/        vazia por padrão (ver "materialização" abaixo)
```

**A montagem da placa (stitching) foi removida.** O pipeline termina nos
quadrantes recortados.

### Como o quadrante é encontrado

A foto do microscópio pega o quadrante alvo **mais pedaços dos vizinhos**. O
detector acha as linhas da grade impressa na armadilha e corta entre elas, nos
quatro lados. Quatro decisões sustentam isso:

| Etapa | O que faz | Por que |
|---|---|---|
| **Blackhat no brilho** | realça só faixas escuras **estreitas** | binarizar o cinza confundia "linha da grade" com "fundo escuro" — e o azul já é escuro em tons de cinza. Era essa a causa do recorte ruim nas azuis |
| **Correção de inclinação** | busca tipo Radon, ±3° | 1° de giro já espalha a linha por dezenas de colunas e apaga o pico da projeção |
| **Perfil por coluna, critério absoluto** | vale como linha a coluna coberta por ≥ X% da foto | "X% do pico mais forte" fazia sujeira virar linha quando a foto tinha pouca grade |
| **Escolha geométrica do par** | entre linhas **consecutivas**, a de largura plausível e meio mais central | "a linha mais escura ganha" pulava uma linha e entregava dois quadrantes colados |

Quando uma linha está ocluída (uma folha por cima, sujeira), o passo da grade é
emprestado do outro eixo — a célula é próxima de quadrada. Se ainda assim não
der, a foto sai inteira e marcada como *sem detecção*: o pipeline **nunca**
inventa um recorte.

O corte nos quatro lados importa: a célula da grade (~4400 px) é menor que a
altura da foto (5400 px), então entregar a altura inteira deixava a linha
horizontal e tiras dos quadrantes de cima e de baixo dentro do resultado.

### 1 foto de entrada = 1 arquivo de saída

O lote **não** é replicado pelas pastas do pipeline. Jogue 30 fotos em
`01_entrada_bruta/` e o resultado são 30 quadrantes em `03_recortadas/` — mais
nada. Renomear não exige escrever um segundo lote em disco: o recorte **sempre**
grava um arquivo novo, então o nome novo é aplicado direto nele e o quadrante já
nasce como `VARD1.png`. É isso que a estratégia `virtual` faz, e ela é o
**default de todos os modos**.

Se quiser mesmo ver as fotos renomeadas em `02_renomeadas/`, peça
explicitamente com `--materializar` — e saiba o que cada opção custa:

| Estratégia | O que faz | Custo em disco | Quando usar |
|---|---|---|---|
| `virtual` | não escreve nada; o nome vai direto no recorte | zero | **default de todos os modos** |
| `hardlink` | cria um link em `02_renomeadas` | zero (mesmo volume) | quer ver as renomeadas sem duplicar |
| `copiar` | cópia byte-a-byte + conferência MD5 | **1× o lote inteiro** | auditoria/rastreabilidade |
| `mover` | move o arquivo (esvazia a entrada) | zero | pasta de entrada é descartável |

Qualquer estratégia que duplique o lote aparece como aviso no log e como a linha
`Copias em 02_renomeadas` no sumário final — se ela estiver zerada, nada foi
replicado.

---

## Modos de operação

Escolha com `--modo`. **A única coisa que muda entre os modos é o NOME do
arquivo de saída** — o recorte é bit-a-bit o mesmo.

| Modo | Nomes gerados | Limite de fotos | Materialização default |
|---|---|---|---|
| `grid` | `a1, a2 … d10` (posição na placa) | 40 (as posições do grid) | `virtual` |
| `sequencial` | `VARD1, VARD2, VARD3 …` | nenhum | `virtual` |
| `recorte` | preserva o nome de origem | nenhum | `virtual` |

```powershell
# O padrão de sempre: 40 fotos viram a1..d10 recortadas
python scripts/run_pipeline.py --modo grid

# Só renomear (VARD1, VARD2…) e recortar — para lotes de qualquer tamanho
python scripts/run_pipeline.py --modo sequencial

# Só recortar, mantendo os nomes
python scripts/run_pipeline.py --modo recorte
```

No modo `grid`, fotos que passarem das 40 posições **não são processadas** — e
isso aparece como `ERROR` no log e no sumário (`Ignoradas`), nunca como um
descarte silencioso. Lote maior que o grid → use `--modo sequencial`.

---

## Requisitos

| Item | Versão |
|---|---|
| Sistema operacional | Windows 10/11 (testado no Windows 11) — também roda em Linux/macOS |
| Python | 3.11 ou superior (validado em 3.14.4) |
| OpenCV | linha **4.x** (`>=4.8.0,<5.0.0`) |
| RAM | ~300 MB por worker com fotos de 9600×5400 (ver [Escala](#escala-milhares-de-fotos)) |
| Disco | o recorte em PNG lossless ocupa ~30 MB por foto de 51 MP |

> **Por que OpenCV 4.x e não 5.x?** A calibração dos parâmetros de detecção foi
> feita no Colab com OpenCV 4. O `requirements.txt` trava a major version para
> que um `pip install -U` não troque silenciosamente o motor de processamento e
> mude o resultado do recorte.

---

## Setup

```powershell
# 1. Clone o repositório
git clone https://github.com/hadryan89/Yellow-Trap-Intelligence.git yellowtrap_pipeline
cd yellowtrap_pipeline

# 2. Crie o ambiente virtual
py -m venv .venv

# 3. Ative o ambiente (PowerShell)
.\.venv\Scripts\Activate.ps1
#    (no cmd.exe:  .venv\Scripts\activate.bat)
#    (no Linux/macOS:  source .venv/bin/activate)

# 4. Instale as dependências
pip install -r requirements.txt

# 5. Crie a estrutura de pastas e confira o ambiente
python scripts/setup_inicial.py
```

> Se o PowerShell bloquear a ativação com *"execução de scripts foi
> desabilitada"*, rode uma vez:
> `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

O `setup_inicial.py` cria as pastas, lista as dependências instaladas e — o mais
importante — **confere se os parâmetros de calibração continuam com os valores
validados no Colab**, avisando em vermelho se algum foi alterado.

### VS Code

Selecione o interpretador do projeto: `Ctrl+Shift+P` → *Python: Select
Interpreter* → `.venv\Scripts\python.exe`.

### Instalação opcional como pacote

```powershell
pip install -e .
```

Cria os comandos `yellowtrap-pipeline`, `yellowtrap-recorte` e
`yellowtrap-watcher` e dispensa o ajuste de `sys.path` feito pelos scripts.

---

## Comandos rápidos

| Comando | O que faz |
|---|---|
| `python scripts/setup_inicial.py` | cria as pastas e valida o ambiente/calibração |
| `python scripts/run_pipeline.py --modo grid` | padrão histórico: a1..d10 + recorte |
| `python scripts/run_pipeline.py --modo sequencial` | VARD1, VARD2… + recorte, sem limite de quantidade |
| `python scripts/run_pipeline.py --simular` | mostra o plano de nomes sem gravar nada |
| `python scripts/run_apenas_recorte.py` | só o recorte, preservando os nomes |
| `python scripts/watcher.py` | vigia `data/01_entrada_bruta/` e processa lotes automaticamente |
| `python scripts/watcher.py --uma-vez` | um único ciclo do watcher e sai (cron / n8n) |
| `python -m pytest` | suíte completa de testes |
| `python tests/fixtures/gerar_fixtures.py` | regera as imagens sintéticas de teste |

Fluxo típico do dia a dia:

```powershell
.\.venv\Scripts\Activate.ps1                   # ativa o ambiente
# copie as fotos para data\01_entrada_bruta\
python scripts/run_pipeline.py --modo grid     # quadrantes em data\03_recortadas\
```

**Código de saída** (útil no n8n / Agendador de Tarefas):

| Código | Significa |
|---|---|
| `0` | lote concluído sem nenhuma falha |
| `1` | falha estrutural — pasta inexistente, lote vazio, nada processado |
| `2` | lote concluído, mas com falhas individuais — confira `data/_falhas/` |

---

## Como usar

### Modo batch (execução única, sob demanda)

```powershell
# Pasta de entrada diferente e 12 processos
python scripts/run_pipeline.py --modo sequencial --entrada "D:\envio_42" --workers 12

# Saída em outra pasta (um job por pasta, por exemplo)
python scripts/run_pipeline.py --modo sequencial --saida "D:\saida\job_42"

# Segundo envio continua a numeração do primeiro (…41, 42, 43)
python scripts/run_pipeline.py --modo sequencial --continuar

# Retomar um lote interrompido: pula o que já foi recortado
python scripts/run_pipeline.py --modo sequencial --retomar

# Ver o de-para antes de processar (não grava nada)
python scripts/run_pipeline.py --modo sequencial --simular

# Amostra rápida das 20 primeiras fotos
python scripts/run_pipeline.py --modo sequencial --limite 20

# Prefixo/dígitos próprios: LAV000001
python scripts/run_pipeline.py --modo sequencial --prefixo LAV

# Contador com largura fixa (LAV000001) em vez de LAV1
python scripts/run_pipeline.py --modo sequencial --prefixo LAV --digitos 6

# Quadrantes em TIFF LZW em vez de PNG
python scripts/run_pipeline.py --formato tiff

# Grid guardando as renomeadas e o ZIP de integridade
python scripts/run_pipeline.py --modo grid --materializar copiar --zip
```

### Modo watcher (contínuo, vigiando a pasta)

```powershell
python scripts/watcher.py                               # grid, lote de 40
python scripts/watcher.py --modo sequencial --tamanho-lote 0
```

O watcher fica rodando e:

1. Detecta arquivos novos em `data/01_entrada_bruta/` via `watchdog`.
2. Só considera um arquivo pronto depois que o **tamanho fica estável por 2
   ciclos** — evita processar foto pela metade enquanto o cartão ainda copia.
3. Fecha o lote de dois jeitos, conforme a operação:
   - **por contagem** (`--tamanho-lote 40`, default): agrupa por prefixo do nome
     (`IMG_001…IMG_040` → grupo `IMG_`) e só dispara com o grupo completo. Lote
     incompleto **nunca** é processado — avisa a cada 30 s quantas fotos faltam;
   - **por quietude** (`--tamanho-lote 0`): passados 30 s sem chegar arquivo
     novo, processa tudo que estiver estável — sejam 12 ou 12.000 fotos. É o
     modo certo para "alguém subiu uma pasta inteira".
4. Isola o lote em `data/01_entrada_bruta/_lotes/<lote_id>/` e dispara o
   pipeline.
5. Grava o histórico em `.watcher_state.json` — reiniciar o watcher não
   reprocessa nada.

```powershell
python scripts/watcher.py --uma-vez          # roda um ciclo e sai (cron / n8n)
python scripts/watcher.py --workers 12 --retomar
python scripts/watcher.py --forcar           # ignora o histórico e reprocessa
python scripts/watcher.py --resetar-estado   # apaga o .watcher_state.json
```

Encerre com `Ctrl+C` — o estado é salvo antes de sair.

---

## Usando como biblioteca

Este código foi feito para viver dentro de um sistema maior (API, fila, n8n).
O ponto de entrada é um só, e toda a configuração de uma execução cabe num
objeto serializável:

```python
from src.opcoes import OpcoesProcessamento
from src.pipeline import executar_processamento

opcoes = OpcoesProcessamento(
    modo="sequencial",                 # "grid" | "sequencial" | "recorte"
    pasta_entrada="D:/uploads/job_42",
    pasta_recortadas="D:/saida/job_42",
    workers=12,
    continuar_numeracao=True,          # não colide com o acervo já existente
    pular_existentes=True,             # retomada barata
    contexto={"job": 42, "usuario": "app"},   # carimbo livre, volta no JSON
)

sumario = executar_processamento(opcoes)

sumario.sucesso          # rodou até o fim e produziu quadrantes
sumario.sem_falhas       # ...e nenhuma foto ficou pelo caminho
sumario.recortadas_ok    # contadores exatos
sumario.to_dict()        # pronto para POST / banco / fila
```

Quer só o de-para, sem processar? O plano de nomeação é uma peça isolada:

```python
from src.renomeacao import planejar_nomeacao
from src.utils import listar_imagens

plano = planejar_nomeacao(listar_imagens("D:/uploads/job_42"), modo="sequencial")
plano.mapeamento   # [('DSC0001.JPG', 'VARD1.jpg'), ...]
```

Outras entradas úteis: `src.pipeline.processar_pasta(pasta, modo=...)`,
`src.pipeline.etapa_recorte(sumario, itens, opcoes)`,
`src.recorte.processar_foto(caminho, nome_saida=...)`.

`executar_pipeline_completo(...)`, a assinatura antiga, continua funcionando
(sem a etapa de stitching).

---

## Escala: milhares de fotos

O pipeline foi dimensionado para lotes de milhares de imagens. O que sustenta
isso, na prática:

| Ponto | Como é tratado |
|---|---|
| **I/O da renomeação** | a estratégia `virtual` não copia nada — o nome novo vai direto no arquivo do recorte. Um lote de 2.000 fotos de 12 MB deixa de escrever ~24 GB inúteis |
| **Cópia com MD5** (quando pedida) | o MD5 da origem é calculado **durante** a cópia: 2 leituras em vez de 3, e as cópias rodam em várias threads (I/O-bound) |
| **Fila de tarefas** | submissão em janela (`workers × 4`): 50.000 fotos não viram 50.000 `Future` vivos no processo pai |
| **Resultados** | acima de 500 itens a agregação é incremental (callback) — a memória do processo pai fica constante |
| **Logs dos workers** | limitados a `INFO` (cada registro atravessa uma fila entre processos) |
| **Listagem de pastas** | `os.scandir` em vez de `iterdir` + `stat` por arquivo |
| **Sumário** | contadores exatos + detalhamento limitado a 200 falhas: um lote inteiro ruim não gera um JSON de centenas de MB |
| **Retomada** | `--retomar` pula quem já tem quadrante na saída — interrupção não custa o lote inteiro |
| **Estado do watcher** | histórico podado nos 50.000 arquivos mais recentes |
| **Worker morto (OOM)** | se o pool quebrar, os itens pendentes são reprocessados sequencialmente em vez de perder o lote |

### Dimensionamento

Medido nesta máquina (12 CPUs) com as fotos reais de produção — 9600×5400 px
(51 MP), ~12 MB por JPEG:

| Grandeza | Medição |
|---|---|
| Pico de RAM **por worker** | ~300 MB (imagem decodificada = 155 MB + temporários) |
| RAM do processo **pai** | 45 MB, constante — igual em 40 e em 600 fotos |
| 40 fotos · 6 workers · PNG | 44,6 s → **0,90 foto/s** |
| 600 fotos · 10 workers · `jpg_max` | 3 min 54 s → **2,56 foto/s** |
| Saída por quadrante | ~30 MB em PNG lossless · ~11 MB em `jpg_max` |

Consequências práticas para um lote de **2.000 fotos**:

- **tempo**: ~37 min com 6 workers em PNG; ~13 min com 10 workers em `jpg_max`;
- **disco**: ~60 GB em PNG. Se o destino for treino/inferência e não arquivo
  científico, `--formato jpg_max` derruba para ~22 GB;
- **RAM**: `--workers` × ~300 MB — 12 workers cabem folgados em 8 GB. O limite
  real costuma ser CPU e disco, não memória.

> Regra de bolso: `workers = min(CPUs - 1, RAM_livre_GB / 0,5)`.

---

## Estrutura do projeto

```
yellowtrap_pipeline/
├── .venv/                       ambiente virtual
├── config/
│   └── settings.py              TODOS os parâmetros ajustáveis
├── src/
│   ├── opcoes.py                OpcoesProcessamento (contrato de entrada)
│   ├── renomeacao.py            Etapa 1 (plano de nomes + materialização)
│   ├── recorte.py               Etapa 2 (detecção de grade + crop, 4 lados)
│   ├── exportacao.py            gravação dos quadrantes
│   ├── paralelismo.py           wrapper de ProcessPoolExecutor
│   ├── pipeline.py              orquestração das 2 etapas
│   └── utils.py                 logging, memória, falhas, sumário
├── data/
│   ├── 01_entrada_bruta/        fotos originais (+ _lotes/ com o histórico)
│   ├── 02_renomeadas/           vazia por padrão (só com `--materializar`)
│   ├── 03_recortadas/           quadrantes limpos  ← entrega final
│   ├── _relatorios/             <lote_id>/sumario.json
│   ├── _falhas/                 fotos problemáticas + JSON do motivo
│   └── _zips/                   ZIPs de integridade (se habilitado)
├── logs/pipeline.log            rotativo: 10 MB × 5 arquivos
├── scripts/
│   ├── setup_inicial.py
│   ├── run_pipeline.py          entrada principal (--modo)
│   ├── run_apenas_recorte.py
│   └── watcher.py
└── tests/
    ├── fixtures/gerar_fixtures.py    gera as imagens sintéticas (amarela e azul)
    ├── test_opcoes.py                contrato de entrada
    ├── test_renomeacao.py
    ├── test_recorte.py               detecção da grade e crop
    ├── test_recorte_acervo.py        regressão contra as fotos reais em data/
    └── test_pipeline.py              integração + paralelismo + modos
```

Nos módulos `src/`, os blocos marcados como

```python
# ---------------------------------------------------------------------------
# Funcoes validadas no Colab - NAO ALTERAR A LOGICA
# ---------------------------------------------------------------------------
```

são cópias literais do notebook. Tudo que veio depois (orquestração, logging,
workers) fica em seções separadas, abaixo, e **não toca no miolo do algoritmo**.

---

## Configuração

Todos os parâmetros vivem em **`config/settings.py`**. Nenhum outro arquivo tem
número mágico.

### Parâmetros calibrados — não altere sem revalidar

Foram **medidos sobre o acervo real** (65 fotos azuis + 40 amarelas). Todos são
fracionários, então acompanham o tamanho da foto.

```python
RECORTE_FATOR_DETECCAO       = 0.125   # detecção roda em 12,5% do tamanho
RECORTE_LARGURA_MINIMA_DETECCAO = 900  # piso, em px, da cópia de detecção
RECORTE_MARGEM_FRAC          = 0.004   # folga a partir da beirada da linha
RECORTE_EIXOS                = 'ambos' # recorta os 4 lados
RECORTE_PERFIL_PADRAO        = 'auto'  # atende amarela e azul
RECORTE_ESPESSURA_MAX_FRAC   = 0.04    # kernel do blackhat
RECORTE_INCLINACAO_MAX_GRAUS = 3.0     # faixa da correção de inclinação
RECORTE_PONTE_FRAC           = 0.04    # costura falhas dentro da linha
RECORTE_DISTANCIA_MIN_FRAC   = 0.02    # duas colunas assim perto = 1 pico
RECORTE_NIVEIS               = ((0.55, 0.50), (0.40, 0.40),
                                (0.28, 0.30), (0.18, 0.22))
RECORTE_FORCA_RELATIVA       = 0.55    # pico fraco demais = sujeira
RECORTE_FORCA_MINIMA         = 0.35    # piso absoluto de uma borda
RECORTE_FORCA_ANCORA         = 0.55    # piso da linha que deduz o par inteiro
RECORTE_CORTAR_MOLDURA       = True    # apara o que não é papel da armadilha
```

Dois testes protegem esses valores: `test_parametros_de_calibracao_preservados`
**quebra a suíte se qualquer um mudar** e `tests/test_recorte_acervo.py` refaz a
medição contra as fotos reais. Mexeu em algo aqui? rode:

```powershell
python -m pytest tests/test_recorte_acervo.py
```

Ele é pulado automaticamente quando o acervo não está no disco.

### Parâmetros que você pode ajustar à vontade

| Parâmetro | Default | O que faz |
|---|---|---|
| `MODO_PADRAO` | `'grid'` | modo usado quando ninguém passa `--modo` |
| `SEQUENCIAL_PREFIXO` | `'VARD'` | prefixo do nome sequencial |
| `SEQUENCIAL_DIGITOS` | `1` | largura **mínima** do contador: `1` → `VARD1, VARD2, VARD10`; `7` → `VARD0000001…` |
| `SEQUENCIAL_INICIO` | `1` | número da primeira foto do lote — o acervo começa em `VARD1`, não existe `VARD0` |
| `SEQUENCIAL_CONTINUAR_NUMERACAO` | `False` | continua a numeração do acervo existente |
| `RENOMEACAO_ESTRATEGIA` | `virtual` em todos os modos | `virtual` / `hardlink` / `copiar` / `mover` — só `virtual` e `mover` não duplicam o lote |
| `RENOMEACAO_VERIFICAR_MD5` | `True` | conferência da cópia (só afeta `copiar`) |
| `RECORTE_FORMATO_SAIDA` | `'png'` | `png`, `tiff`, `jpg_max` |
| `RECORTE_PERFIL_PADRAO` | `'auto'` | cor da armadilha: `auto` (as duas), `amarela`, `azul`. Só aperta a faixa de largura aceita |
| `RECORTE_EIXOS` | `'ambos'` | `ambos` recorta os 4 lados; `so_vertical` devolve a altura inteira (comportamento histórico) |
| `RECORTE_CORTAR_MOLDURA` | `True` | apara as pontas que não são papel da armadilha (fundo do microscópio, sombra) |
| `RECORTE_PULAR_EXISTENTES` | `False` | retomada automática |
| `LIMPAR_PASTAS_INTERMEDIARIAS` | `False` | esvazia `03_recortadas` antes do lote |
| `NUM_WORKERS` | `None` | processos paralelos; `None` = CPUs − 1 |
| `PARALELISMO_JANELA_POR_WORKER` | `4` | tarefas em voo por worker |
| `PARALELISMO_LIMIAR_STREAMING` | `500` | a partir daqui a agregação é incremental |
| `SUMARIO_MAX_FALHAS_DETALHADAS` | `200` | detalhamento das falhas no sumário |
| `WATCHER_CICLOS_ESTABILIDADE` | `2` | ciclos com tamanho estável antes de aceitar o arquivo |
| `RECORTE_FALHA_DETECCAO_E_ERRO` | `False` | ver [Tratamento de falhas](#tratamento-de-falhas) |
| `LOG_NIVEL_CONSOLE` | `'INFO'` | verbosidade do console |

### Mudar o local dos dados sem editar código

```powershell
$env:YELLOWTRAP_DATA_DIR = "D:\yellowtrap\data"
python scripts/run_pipeline.py --modo sequencial
```

---

## Como interpretar os logs

Formato: `[data hora] [NÍVEL] [módulo] mensagem`

- **Console** — colorido (`colorlog`), nível `INFO`.
- **Arquivo** — `logs/pipeline.log`, nível `DEBUG`, rotativo (10 MB × 5
  arquivos).

Os processos paralelos **não escrevem no arquivo diretamente** — eles mandam os
registros por uma fila para o processo principal, que grava. Isso evita corrupção
do log por escrita concorrente no Windows.

### Níveis

| Nível | Significa | O que fazer |
|---|---|---|
| `INFO` | andamento normal | nada |
| `WARNING` | processou, mas com ressalva (quadrante sem detecção, lote incompleto) | **conferir os quadrantes citados** |
| `ERROR` | uma foto falhou (ou ficou de fora do grid); o lote continuou | ver `data/_falhas/` |
| `CRITICAL` | falha estrutural | ver o traceback no `pipeline.log` |

### Sumário final

Impresso no console e salvo em `data/_relatorios/<lote_id>/sumario.json`:

```
====================================================================
SUMARIO DO LOTE 20260815_110928  (modo: grid)
====================================================================
  Fotos de entrada .............. 40
  Nomeadas ...................... 40
  Recortadas com sucesso ........ 40
  Recortadas SEM deteccao ....... 1       ← exige conferência visual
  Falhas ........................ 0
  Pasta de saida ................ ...\data\03_recortadas
  Memoria (fim do lote) ......... 45 MB
  Tempo total ................... 44.61s
  Throughput .................... 0.90 foto/s
====================================================================
```

A linha que mais importa na rotina é **`Recortadas SEM deteccao`**: qualquer
valor maior que zero significa que aquele quadrante saiu com a foto inteira, sem
recorte.

---

## Tratamento de falhas

**O pipeline nunca aborta por causa de uma foto ruim.** Cada worker é isolado; o
erro é registrado e o lote segue.

Toda falha gera um `.json` em `data/_falhas/<lote_id>/`:

```json
{
  "arquivo": "...\\data\\01_entrada_bruta\\DSC0005.JPG",
  "arquivo_nome": "DSC0005.JPG",
  "etapa": "recorte",
  "motivo": "Imagem ilegivel (cv2.imread retornou None)",
  "lote_id": "20260815_110928",
  "timestamp": "2026-08-15T11:09:42",
  "detalhes": {},
  "arquivo_em_falhas": "...\\data\\_falhas\\20260815_110928\\DSC0005.JPG"
}
```

### Os tipos de falha

| Situação | O que acontece | Arquivo movido? |
|---|---|---|
| **Imagem ilegível/corrompida** | quadrante não é gerado; entra em `Falhas` | só se estiver numa pasta intermediária (ver abaixo) |
| **MD5 divergente na renomeação** | cópia rejeitada | sim, vai para `_falhas/` |
| **Detecção da grade falhou** | quadrante é salvo com a **imagem cheia, sem recorte** + `WARNING` | **não** — só o JSON de diagnóstico |
| **Foto além das 40 posições (modo grid)** | não é processada; entra em `Ignoradas` + `ERROR` | não |

> **O arquivo original do usuário nunca é movido.** Quando o recorte lê direto
> da pasta de entrada (estratégia `virtual`), a foto problemática fica onde
> está e só o JSON vai para `_falhas/`. O arquivo só é movido quando o que
> falhou é uma cópia em `02_renomeadas`, que o pipeline mesmo criou.

O terceiro caso preserva o comportamento validado no Colab: sem as linhas da
grade, a função devolve a imagem inteira. Para tratá-lo como falha dura (nenhum
quadrante gerado, foto movida para `_falhas/`):

```python
RECORTE_FALHA_DETECCAO_E_ERRO = True
```

Em lotes muito grandes, `RECORTE_REGISTRAR_JSON_SEM_DETECCAO = False` evita
gerar milhares de JSONs de diagnóstico — os contadores do sumário continuam.

### Reprocessando uma falha

```powershell
# 1. corrija/refotografe a foto e coloque de volta na pasta de entrada
# 2. rode com --retomar: só o que falta é processado
python scripts/run_pipeline.py --modo sequencial --retomar
```

---

## Testes

```powershell
python -m pytest                       # suíte completa
python -m pytest -m "not lento"        # pula os testes pesados
python -m pytest -m "not acervo"       # pula a regressão contra as fotos reais
python -m pytest tests/test_recorte.py -v
```

Os testes marcados `acervo` rodam sobre as fotos de verdade em `data/` e se
**pulam sozinhos** quando elas não estão no disco (CI, clone limpo).

As imagens de teste são **geradas** por `tests/fixtures/gerar_fixtures.py` na
primeira execução — nada de binário no repositório.

### O que os testes travam

- **o recorte é idêntico nos três modos** e igual ao do recorte avulso
  (`test_recorte_identico_em_todos_os_modos`) — mudar de modo muda o nome do
  arquivo e nada mais;
- os **parâmetros de calibração**, e que as funções públicas não têm default
  duplicado no código (tudo resolve em `settings.py`);
- **a cor não entra na conta**: a mesma geometria em amarelo e em azul tem que
  dar o **mesmo crop box** (`test_amarela_e_azul_dao_o_mesmo_recorte`);
- a **detecção**: crop entre as linhas da célula central nos dois eixos, linha
  da grade fora do quadrante, linha de vizinho não vira borda, moldura preta
  aparada, inclinação de 1,5° corrigida;
- **regressão contra o acervo real** (`test_recorte_acervo.py`): toda foto de
  armadilha vira quadrante, a largura entregue é consistente (desvio < 0,02) e
  **nenhuma linha da grade sobra dentro do quadrante**;
- **crop em resolução cheia**: a saída é bit-a-bit igual a `original[y1:y2, x1:x2]`;
- **lossless**: PNG e TIFF relidos do disco batem exatamente com o array em memória;
- **ordem natural** (`img2` antes de `img10`) e cópia byte-a-byte com MD5;
- **plano sequencial** sem teto de quantidade, com índice inicial e continuação
  da numeração;
- **estratégias de materialização**: `virtual` não escreve nada, `mover` esvazia
  a origem, cópia em paralelo não embaralha a ordem;
- **nenhuma duplicação do lote**: 30 fotos de entrada produzem 30 arquivos de
  saída nos três modos (`test_n_fotos_entram_n_arquivos_saem`) e nenhum modo
  materializa `02_renomeadas` por padrão;
- **robustez**: foto corrompida não derruba o lote, arquivo sumido no meio da
  cópia não derruba a etapa, ordem dos resultados preservada no paralelismo,
  janela de submissão não perde item, retomada não reprocessa.

### Diferença em relação à versão anterior

O enquadramento **mudou de propósito** — não é mais byte-a-byte igual ao da
versão anterior, e não deveria ser:

- o recorte agora acontece nos **quatro lados** (antes, só as laterais);
- a escolha do par de linhas passou a ser **geométrica**, o que corrigiu as
  fotos em que o recorte engolia dois quadrantes;
- a armadilha **azul** passou de 7/65 para 65/65 recortes limpos.

Os pixels entregues continuam sendo uma fatia exata da imagem original — o que
mudou foi *onde* a fatia é feita, não *como*.

---

## Preservação de qualidade

| Requisito | Como é garantido |
|---|---|
| Leitura em resolução cheia | `cv2.imread` sem downscale; a redução de 12,5% só existe em memória para detectar as linhas |
| Crop em resolução cheia | a detecção devolve **frações** (`crop_box_frac`), aplicadas na imagem original |
| Sem re-encoding intermediário | o crop é uma *slice* NumPy da imagem decodificada; nada é recomprimido antes de salvar |
| Saída lossless | PNG (`IMWRITE_PNG_COMPRESSION=1`) ou TIFF LZW (`IMWRITE_TIFF_COMPRESSION=5`) |
| Renomear não toca no pixel | o nome novo é só o caminho de gravação; com `copiar`, MD5 conferido dos dois lados |
| ZIP sem recompressão | `zipfile.ZIP_STORED` + verificação de MD5 dos bytes dentro do ZIP |

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'config'`**
O ambiente virtual não está ativo, ou você chamou o script de um jeito que não
passa pela raiz do projeto. Ative com `.\.venv\Scripts\Activate.ps1` e rode a
partir da pasta do projeto. Alternativa definitiva: `pip install -e .`

**`cv2.imread` retorna `None` / "Imagem ilegivel"**
Três causas comuns: (1) arquivo corrompido no cartão; (2) extensão que o OpenCV
não abre (HEIC, RAW) — converta para JPEG/PNG antes; (3) caminho com acentos. O
terceiro caso já tem resgate automático via `imread_fallback`.

**Muitos `Recortadas SEM deteccao`**
A grade não está sendo encontrada. Nessa ordem:

1. Confira o `origem` no JSON de diagnóstico em `data/_falhas/`. As fotos que
   entram aqui costumam ser as que **não são de armadilha** (fora de foco, sem
   grade no enquadramento) — nesse caso a recusa está certa.
2. Verifique iluminação e contraste: as linhas da grade precisam ficar
   nitidamente mais escuras que o papel. A cor do papel **não** importa.
3. Se o lote inteiro está difícil, tente travar a cor:
   `--perfil azul` ou `--perfil amarela`. Isso aperta a faixa de largura aceita
   e costuma resolver sem tocar na calibração.

**Não mude os parâmetros de calibração para "resolver"**: isso muda o recorte de
todas as fotos boas também. Se mudar mesmo assim, rode
`python -m pytest tests/test_recorte_acervo.py` antes de subir.

**O recorte engoliu dois quadrantes / veio uma linha preta no meio**
Era o sintoma do detector antigo em armadilha azul, e está coberto por teste
(`test_nenhuma_linha_da_grade_sobra_no_quadrante`). Se voltar a acontecer,
guarde a foto e rode aquele teste apontando para ela — o diagnóstico sai pronto.

**Fotos ficaram de fora / `Ignoradas` > 0**
Você está no modo `grid`, que tem exatamente 40 posições. Rode com
`--modo sequencial`.

**Nomes colidindo entre envios**
No modo sequencial, cada execução recomeça em `VARD1` e sobrescreve o
envio anterior. Use `--continuar` (ou uma `--saida` por job).

**`MemoryError` / o processo morre no meio**
Workers demais para a RAM: cada um chega a ~300 MB com fotos de 51 MP (mais, se
as suas forem maiores). Baixe `--workers`. Se o pool quebrar, o pipeline
reprocessa o que faltou sequencialmente e avisa no log — o lote não se perde.

**Está lento**
Confira, nesta ordem: (1) `--workers` (o default deixa 1 core livre); (2) a
estratégia de renomeação — `copiar` com MD5 lê e escreve o lote inteiro antes de
começar, use `virtual`; (3) o formato de saída — PNG lossless de 51 MP é caro,
`--formato jpg_max` é ~10× menor e mais rápido de gravar; (4) se as fotos estão
num disco de rede, copie para o disco local antes.

**Disco enchendo**
PNG lossless de um quadrante de 51 MP dá ~30 MB. 2.000 fotos = ~60 GB. Use
`--formato jpg_max` ou aponte a saída para outro volume com `--saida`.

**A barra de progresso aparece como "erro" no PowerShell**
O `tqdm` escreve na saída de erro padrão e o PowerShell marca isso como
`NativeCommandError`. É cosmético. Para silenciar,
`PARALELISMO_CHUNK_TQDM = False` em `settings.py`.

**Watcher não dispara**
Confira: (1) no modo por contagem, o grupo já tem 40 fotos com o **mesmo
prefixo**? Nomes misturados formam grupos diferentes — para envios heterogêneos
use `--tamanho-lote 0`; (2) as fotos já foram processadas antes? Veja
`.watcher_state.json` — use `--forcar`; (3) `watchdog` está instalado? Sem ele o
watcher ainda funciona, mas só por polling.

---

## Integração com n8n / Railway

**Webhook de fim de lote** — defina a variável de ambiente e o sumário completo
é enviado por `POST` (JSON) ao final de cada execução:

```powershell
$env:YELLOWTRAP_WEBHOOK_URL = "https://SEU-N8N.exemplo.com/webhook/yellowtrap"
python scripts/watcher.py --modo sequencial --tamanho-lote 0
```

O corpo do POST é o mesmo conteúdo do `sumario.json` (lote, modo, contagens,
falhas, throughput, duração). Usa só a stdlib — sem dependência extra.

**Execução agendada** — `python scripts/watcher.py --uma-vez` roda um ciclo e sai
com código `0`/`1`/`2`, adequado para Agendador de Tarefas do Windows, cron ou um
nó *Execute Command* do n8n.

**Embutido no sistema** — veja [Usando como biblioteca](#usando-como-biblioteca):
`OpcoesProcessamento` + `executar_processamento` cobrem os três modos sem
subprocesso.

**Próximo passo natural** — `data/03_recortadas/` é exatamente a entrada que o
modelo de deep learning espera: quadrantes limpos, com nome estável e
rastreável pelo `sumario.json`.

---

## Mudanças recentes

### O recorte passou a atender a armadilha azul

**Antes:** o detector binarizava a foto em tons de cinza para achar as linhas
pretas da grade. Isso funciona quando o fundo é amarelo (claro em cinza), mas o
azul da armadilha já é escuro em cinza — o algoritmo confundia fundo com linha,
escolhia como borda "a linha mais escura de cada lado" e entregava dois
quadrantes colados, uma faixa da moldura, ou a foto quase inteira. Só **7 das
65** fotos azuis saíam com recorte utilizável.

**Agora:** a detecção é cega à cor. Ela realça faixas escuras estreitas
(blackhat), corrige a inclinação da grade, mede as linhas com critério absoluto
e escolhe o par **consecutivo** cuja largura é plausível para um quadrante.

| | Antes | Agora |
|---|---|---|
| Azuis com recorte limpo | 7 / 65 | **65 / 65** |
| Amarelas com recorte limpo | 35 / 40 | **40 / 40** |
| Dispersão da largura (azul) | ±0,152 | **±0,010** |
| Dispersão da largura (amarela) | ±0,051 | **±0,009** |

Junto vieram três mudanças de comportamento:

- **o recorte acontece nos quatro lados.** A célula da grade (~4400 px) é menor
  que a altura da foto (5400 px), então entregar a altura inteira deixava a
  linha horizontal e tiras dos quadrantes vizinhos dentro do resultado. Quem
  depende do comportamento antigo tem `RECORTE_EIXOS = 'so_vertical'`;
- **a moldura sai do quadrante.** As pontas escuras que não são papel da
  armadilha (fundo do microscópio, sombra) são aparadas, com trava de 15% por
  ponta para nunca comer quadrante de verdade;
- **`RECORTE_FATOR_DETECCAO` caiu de 0.25 para 0.125.** Todos os parâmetros
  viraram fracionários, então a detecção ficou ~4× mais barata sem mudar o
  resultado.

**Comandos separados por cor?** Não foi preciso: o mesmo comando atende as duas.
O `--perfil {auto,amarela,azul}` existe como trava opcional para lote difícil e
**não muda o algoritmo** — só aperta a faixa de largura aceita.

### O lote deixou de ser replicado em disco

**Antes:** o modo `grid` (o padrão) vinha configurado com a estratégia `copiar`.
Antes de recortar, ele gravava uma cópia byte-a-byte de cada foto em
`02_renomeadas/`. Com o original em `01_entrada_bruta/` e o quadrante em
`03_recortadas/`, o mesmo lote passava a existir **três vezes** em disco — 30
fotos viravam 90 arquivos.

**Agora:** todos os modos usam a estratégia `virtual`. O nome novo é carimbado
direto no arquivo do recorte, que já nasce como `VARD1.png` / `a1.png`. **Uma
foto de entrada gera exatamente um arquivo de saída.**

| | Antes | Agora |
|---|---|---|
| `01_entrada_bruta/` | 30 fotos (suas originais) | 30 fotos (suas originais) |
| `02_renomeadas/` | **30 cópias** | vazia |
| `03_recortadas/` | 30 quadrantes | 30 quadrantes |

A renomeação **não** foi removida — só mudou onde é gravada. Ela continua
acontecendo nos três modos, agora sem o arquivo intermediário.

Conferido com 30 fotos reais de produção: `402 MB` de entrada → `313 MB` de
saída, `02_renomeadas/` vazia, originais intactas.

Se você precisar mesmo das fotos renomeadas em disco (auditoria, conferência
MD5), peça explicitamente — mas saiba que isso volta a duplicar o lote:

```powershell
python scripts/run_pipeline.py --modo grid --materializar copiar
```

Nesse caso o log emite um `WARNING` dizendo quantas cópias extras serão criadas,
e o sumário final ganha a linha `Copias em 02_renomeadas`. Se essa linha não
aparecer, nada foi replicado.

### Numeração sequencial sem zeros à esquerda, começando em 1

**Antes:** `VARD0000001, VARD0000002, VARD0000003 …`
**Agora:** `VARD1, VARD2, VARD3, VARD4 …`

```python
SEQUENCIAL_DIGITOS = 1   # era 7  → largura mínima do contador, sem zeros
SEQUENCIAL_INICIO  = 1   # a primeira foto do lote vira VARD1
```

**Não existe `VARD0`.** A contagem começa em 1, como qualquer conferência
manual espera. Há um teste travando isso (`test_a_contagem_comeca_em_um`).

Dois pontos que valem saber:

- **o número nunca é truncado.** `digitos` é a largura *mínima*, não um limite:
  depois de `VARD9` o nome cresce sozinho para `VARD10`, `VARD100`, `VARD1000`.
  Continua sem teto de quantidade;
- **ordenação alfabética pura embaralha.** Um programa que ordene por texto
  cru mostra `VARD1, VARD10, VARD11, VARD2…`. O Explorer do Windows e o
  próprio pipeline usam ordenação natural, então na prática a sequência aparece
  certa — mas se algum sistema externo do seu fluxo ordenar alfabeticamente, use
  largura fixa nesse caso: `--digitos 7`.

O `--continuar` segue funcionando e **lê os dois formatos**: um acervo antigo
com `VARD0000042` faz o próximo lote começar em `VARD43`, sem colisão.

---

*Grupo Progresso — Setor de Inovação · versão 2.0.0*
