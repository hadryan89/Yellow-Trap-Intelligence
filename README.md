# YellowTrap Pipeline

Processamento automatizado de imagens de armadilhas amarelas (YellowTrap) usadas
na captura e identificação de pragas na lavoura — tripes, afídeos, cigarrinhas,
mosca branca.

**VARD** · Região MATOPIBA (Sebastião Leal/PI)

O sistema recebe as fotos individuais dos quadrantes da placa (capturadas por
câmera/microscópio), recorta cada quadrante e remonta a placa completa por
*image stitching*. A identificação das pragas por *deep learning* é uma etapa
posterior, fora do escopo deste repositório.

Esta é a migração dos 3 protocolos validados no Google Colab para execução local
em VS Code. **A lógica de negócio, os algoritmos e os parâmetros de calibração
foram preservados exatamente como validados** — a refatoração adiciona apenas
modularização, configuração centralizada, logs, paralelismo, watcher e testes.

---

## Índice

- [O pipeline em 3 etapas](#o-pipeline-em-3-etapas)
- [Requisitos](#requisitos)
- [Setup](#setup)
- [Comandos rápidos](#comandos-rápidos)
- [Como usar](#como-usar)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Configuração](#configuração)
- [Como interpretar os logs](#como-interpretar-os-logs)
- [Tratamento de falhas](#tratamento-de-falhas)
- [Testes](#testes)
- [Preservação de qualidade](#preservação-de-qualidade)
- [Troubleshooting](#troubleshooting)
- [Integração com n8n / Railway](#integração-com-n8n--railway)

---

## O pipeline em 3 etapas

```
data/01_entrada_bruta/     DSC0001.JPG ... DSC0040.JPG   (nomes da câmera)
        │
        │  Protocolo 1 — renomeação (cópia byte-a-byte + conferência MD5)
        ▼
data/02_renomeadas/        a1.jpg a2.jpg ... d10.jpg
        │
        │  Protocolo 2 — recorte do quadrante central (PARALELO, N processos)
        ▼
data/03_recortadas/        a1.png a2.png ... d10.png
        │
        │  Protocolo 3 — stitching (layout horizontal 4 linhas × 10 colunas)
        ▼
data/04_placas_montadas/<lote_id>/
                           placa_LOSSLESS.png     ← arquivo mestre
                           placa_CIENTIFICO.tiff  ← abre no ImageJ/Fiji
                           placa_10k.jpg / placa_4k.jpg / placa_1200p.jpg / placa_720p.jpg
                           placa_WEBP.webp
                           sumario.json
```

**Layout da placa montada** — cada letra é uma faixa horizontal, cada número é
uma coluna:

```
[a1  a2  a3  a4  a5  a6  a7  a8  a9  a10]
[b1  b2  b3  b4  b5  b6  b7  b8  b9  b10]
[c1  c2  c3  c4  c5  c6  c7  c8  c9  c10]
[d1  d2  d3  d4  d5  d6  d7  d8  d9  d10]
```

Célula sem quadrante vira um **placeholder amarelo** `(40, 230, 250)` — fica
visualmente óbvio qual quadrante faltou.

---

## Requisitos

| Item | Versão |
|---|---|
| Sistema operacional | Windows 10/11 (testado no Windows 11) — também roda em Linux/macOS |
| Python | 3.11 ou superior (validado em 3.14.4) |
| OpenCV | linha **4.x** (`>=4.8.0,<5.0.0`) |
| RAM | 8 GB para lotes de 40 fotos; 16 GB recomendado |
| Disco | ~3× o tamanho do lote bruto (renomeadas + recortadas + placa) |

> **Por que OpenCV 4.x e não 5.x?** A calibração dos parâmetros de detecção foi
> feita no Colab com OpenCV 4. O `requirements.txt` trava a major version para
> que um `pip install -U` não troque silenciosamente o motor de processamento e
> mude o resultado do recorte. Se um dia for necessário migrar para OpenCV 5,
> rode a suíte de testes antes e compare as placas geradas.

---

## Setup

```powershell
# 1. Clone o repositório
git clone https://github.com/hadryan89/ahdasjdas.git yellowtrap_pipeline
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

Cria os comandos `yellowtrap-pipeline` e `yellowtrap-watcher` e dispensa o ajuste
de `sys.path` feito pelos scripts.

---

## Comandos rápidos

Referência de bolso — cada comando está detalhado nas seções seguintes.

| Comando | O que faz |
|---|---|
| `python scripts/setup_inicial.py` | cria as pastas e valida o ambiente/calibração |
| `python scripts/run_pipeline_completo.py` | roda as 3 etapas de uma vez (modo batch) |
| `python scripts/watcher.py` | vigia `data/01_entrada_bruta/` e processa lotes automaticamente |
| `python scripts/watcher.py --uma-vez` | um único ciclo do watcher e sai (cron / n8n) |
| `python scripts/run_apenas_recorte.py` | só o Protocolo 2 (recorte dos quadrantes) |
| `python scripts/run_apenas_stitching.py` | só o Protocolo 3 (montagem da placa) |
| `python -m pytest` | suíte completa de testes |
| `python tests/fixtures/gerar_fixtures.py` | regera as imagens sintéticas de teste |

Fluxo típico do dia a dia:

```powershell
.\.venv\Scripts\Activate.ps1                   # ativa o ambiente
# copie as 40 fotos para data\01_entrada_bruta\
python scripts/run_pipeline_completo.py        # placa pronta em data\04_placas_montadas\
```

---

## Como usar

### Modo batch (execução única, sob demanda)

Coloque as 40 fotos em `data/01_entrada_bruta/` e rode:

```powershell
python scripts/run_pipeline_completo.py
```

Opções:

```powershell
# Pasta de entrada diferente e 4 processos
python scripts/run_pipeline_completo.py --entrada "D:\fotos\lote_07" --workers 4

# Quadrantes em TIFF LZW em vez de PNG
python scripts/run_pipeline_completo.py --formato tiff

# Gera também o ZIP (ZIP_STORED) das fotos renomeadas
python scripts/run_pipeline_completo.py --zip

# Fotos já nomeadas a1..d10: pula a renomeação
python scripts/run_pipeline_completo.py --pular-renomeacao

# Identificador de lote fixo (em vez do timestamp)
python scripts/run_pipeline_completo.py --lote-id LOTE_2026_08_13_TALHAO_3

# Log DEBUG no console
python scripts/run_pipeline_completo.py --verbose
```

O código de saída é `0` em caso de sucesso e `1` se o lote falhou — útil para
encadear em scripts ou no n8n.

### Modo watcher (contínuo, vigiando a pasta)

```powershell
python scripts/watcher.py
```

O watcher fica rodando e:

1. Detecta arquivos novos em `data/01_entrada_bruta/` via `watchdog`.
2. Agrupa por prefixo do nome (`IMG_001…IMG_040` → grupo `IMG_`).
3. Só considera um arquivo pronto depois que o **tamanho fica estável por 2
   ciclos** — evita processar foto pela metade enquanto o cartão ainda copia.
4. Quando o grupo chega a 40 fotos, isola o lote em
   `data/01_entrada_bruta/_lotes/<lote_id>/` e dispara o pipeline completo.
5. **Lote incompleto NÃO é processado.** O watcher espera indefinidamente e
   avisa a cada 30 s de inatividade quantas fotos faltam:

   ```
   [WARNING] [watcher] Lote INCOMPLETO 'IMG_': 35/40 foto(s) - faltam 5.
                       Aguardando (nada sera processado pela metade).
   ```

6. Grava o histórico em `.watcher_state.json` — reiniciar o watcher não
   reprocessa nada.

Opções:

```powershell
python scripts/watcher.py --uma-vez          # roda um ciclo e sai (cron / n8n)
python scripts/watcher.py --tamanho-lote 20  # placa com outro número de quadrantes
python scripts/watcher.py --workers 6
python scripts/watcher.py --forcar           # ignora o histórico e reprocessa
python scripts/watcher.py --resetar-estado   # apaga o .watcher_state.json
```

Encerre com `Ctrl+C` — o estado é salvo antes de sair.

### Rodar etapas isoladas

```powershell
# Só o recorte (reprocessar sem refazer a renomeação)
python scripts/run_apenas_recorte.py
python scripts/run_apenas_recorte.py --formato tiff --workers 6

# Só o stitching (ex.: depois de trocar um quadrante ruim na mão)
python scripts/run_apenas_stitching.py
python scripts/run_apenas_stitching.py --escala 0.5   # prévia rápida
```

> `--escala` abaixo de 1.0 gera uma placa reduzida. Serve para conferência
> visual rápida — **não use para o arquivo científico.**

---

## Estrutura do projeto

```
yellowtrap_pipeline/
├── .venv/                       ambiente virtual
├── config/
│   └── settings.py              TODOS os parâmetros ajustáveis
├── src/
│   ├── renomeacao.py            Protocolo 1 (+ MD5 + ZIP_STORED)
│   ├── recorte.py               Protocolo 2 (detecção de grade + crop)
│   ├── stitching.py             Protocolo 3 (montagem da placa)
│   ├── exportacao.py            salvamento em múltiplos formatos
│   ├── paralelismo.py           wrapper de ProcessPoolExecutor
│   ├── pipeline.py              orquestração das 3 etapas
│   └── utils.py                 logging, memória, falhas, sumário
├── data/
│   ├── 01_entrada_bruta/        fotos originais (+ _lotes/ com o histórico)
│   ├── 02_renomeadas/           a1.jpg … d10.jpg
│   ├── 03_recortadas/           quadrantes limpos
│   ├── 04_placas_montadas/      <lote_id>/placa_*.png|tiff|jpg|webp
│   ├── _falhas/                 fotos problemáticas + JSON do motivo
│   └── _zips/                   ZIPs de integridade (se habilitado)
├── logs/pipeline.log            rotativo: 10 MB × 5 arquivos
├── scripts/
│   ├── setup_inicial.py
│   ├── run_pipeline_completo.py
│   ├── run_apenas_recorte.py
│   ├── run_apenas_stitching.py
│   └── watcher.py
└── tests/
    ├── fixtures/gerar_fixtures.py    gera as imagens sintéticas de teste
    ├── test_renomeacao.py
    ├── test_recorte.py
    ├── test_stitching.py
    └── test_pipeline.py             integração + paralelismo
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

### Parâmetros calibrados — não altere

Estes valores são o resultado de dezenas de iterações de calibração no Colab.
Alterá-los muda o recorte e invalida a validação:

```python
RECORTE_FATOR_DETECCAO       = 0.25         # detecção roda em 25% do tamanho
RECORTE_MARGEM               = 8            # px afastados da linha detectada
RECORTE_MODO                 = 'so_vertical'
RECORTE_SPAN_V_INICIAL_FRAC  = 0.5
RECORTE_SPAN_H_INICIAL_FRAC  = 0.5
RECORTE_SPAN_MINIMO_FRAC     = 0.15
RECORTE_DILATAR              = True
STITCHING_ESCALA_CARREGAMENTO = 1.0         # resolução cheia
STITCHING_COR_PLACEHOLDER     = (40, 230, 250)
```

Há um teste (`test_parametros_de_calibracao_preservados`) que **quebra a suíte se
qualquer um desses valores for alterado** — de propósito.

### Parâmetros que você pode ajustar à vontade

| Parâmetro | Default | O que faz |
|---|---|---|
| `RECORTE_FORMATO_SAIDA` | `'png'` | formato dos quadrantes: `png`, `tiff`, `jpg_max` |
| `NUM_WORKERS` | `None` | processos paralelos; `None` = CPUs − 1 |
| `EXPORTACAO_RESOLUCOES` | 10k/4k/1200p/720p | resoluções de saída da placa |
| `EXPORTACAO_INCLUIR_*` | `True` | liga/desliga PNG lossless, TIFF, WEBP |
| `WATCHER_INTERVALO_POLL_SEG` | `5` | intervalo entre ciclos do watcher |
| `WATCHER_TIMEOUT_LOTE_INCOMPLETO_SEG` | `30` | intervalo do aviso de lote incompleto |
| `WATCHER_CICLOS_ESTABILIDADE` | `2` | ciclos com tamanho estável antes de aceitar o arquivo |
| `RENOMEACAO_CRIAR_ZIP` | `False` | ZIP de integridade (dobra o uso de disco) |
| `RECORTE_FALHA_DETECCAO_E_ERRO` | `False` | ver [Tratamento de falhas](#tratamento-de-falhas) |
| `LOG_NIVEL_CONSOLE` | `'INFO'` | verbosidade do console |

### Mudar o local dos dados sem editar código

```powershell
$env:YELLOWTRAP_DATA_DIR = "D:\yellowtrap\data"
python scripts/run_pipeline_completo.py
```

---

## Como interpretar os logs

Formato: `[data hora] [NÍVEL] [módulo] mensagem`

- **Console** — colorido (`colorlog`), nível `INFO`.
- **Arquivo** — `logs/pipeline.log`, nível `DEBUG`, rotativo (10 MB × 5
  arquivos: `pipeline.log`, `pipeline.log.1`, … `pipeline.log.5`).

Os processos paralelos **não escrevem no arquivo diretamente** — eles mandam os
registros por uma fila para o processo principal, que grava. Isso evita corrupção
do log por escrita concorrente no Windows.

### Níveis

| Nível | Significa | O que fazer |
|---|---|---|
| `INFO` | andamento normal | nada |
| `WARNING` | processou, mas com ressalva (quadrante sem detecção, lote incompleto, célula com placeholder) | **conferir a placa gerada** |
| `ERROR` | uma foto falhou; o lote continuou | ver `data/_falhas/` |
| `CRITICAL` | falha estrutural | ver o traceback no `pipeline.log` |

### Sumário final

Impresso no console e salvo em `<pasta_de_saída>/sumario.json`:

```
====================================================================
SUMARIO DO LOTE 20260813_140828
====================================================================
  Fotos de entrada .............. 40
  Renomeadas (MD5 conferido) .... 40      ← integridade byte-a-byte OK
  Recortadas com sucesso ........ 40
  Recortadas SEM deteccao ....... 0       ← >0 exige conferência visual
  Falhas ........................ 0
  Quadrantes no stitching ....... 40
  Celulas placeholder ........... 0       ← >0 = buraco amarelo na placa
  Placa montada ................. 3360 x 3200 px (L x A)
  Arquivos gerados .............. 5
  Pasta de saida ................ ...\data\04_placas_montadas\20260813_140828
  Memoria (fim do lote) ......... 50 MB
  Tempo total ................... 5.10s
====================================================================
```

As duas linhas que mais importam na rotina são **`Recortadas SEM deteccao`** e
**`Celulas placeholder`**: qualquer valor maior que zero significa que a placa
final tem quadrante suspeito ou faltando.

---

## Tratamento de falhas

**O pipeline nunca aborta por causa de uma foto ruim.** Cada worker é isolado; o
erro é registrado e o lote segue.

Toda falha gera um `.json` em `data/_falhas/<lote_id>/`:

```json
{
  "arquivo": "...\\data\\02_renomeadas\\a5.png",
  "arquivo_nome": "a5.png",
  "etapa": "recorte",
  "motivo": "Imagem ilegivel (cv2.imread retornou None)",
  "lote_id": "LOTE_FALHAS",
  "timestamp": "2026-08-13T14:09:42",
  "detalhes": {},
  "arquivo_em_falhas": "...\\data\\_falhas\\LOTE_FALHAS\\a5.png"
}
```

### Os três tipos de falha

| Situação | O que acontece | Arquivo movido? |
|---|---|---|
| **Imagem ilegível/corrompida** | quadrante não é gerado; célula vira placeholder | sim, vai para `_falhas/` |
| **MD5 divergente na renomeação** | cópia rejeitada | sim, vai para `_falhas/` |
| **Detecção da grade falhou** | quadrante é salvo com a **imagem cheia, sem recorte** + `WARNING` | **não** — só o JSON de diagnóstico |

O terceiro caso preserva o comportamento validado no Colab: sem as linhas da
grade, a função devolve a imagem inteira. O quadrante entra na placa, mas com
os vizinhos junto — por isso o `WARNING` e o contador `Recortadas SEM deteccao`.

Se preferir que esse caso seja tratado como falha dura (quadrante não gerado,
célula com placeholder amarelo bem visível), mude em `settings.py`:

```python
RECORTE_FALHA_DETECCAO_E_ERRO = True
```

### Reprocessando uma falha

```powershell
# 1. corrija/refotografe o quadrante e coloque em data/02_renomeadas/ com o nome certo
# 2. rode só o recorte
python scripts/run_apenas_recorte.py
# 3. remonte a placa
python scripts/run_apenas_stitching.py
```

---

## Testes

```powershell
python -m pytest              # suíte completa (59 testes)
python -m pytest -m "not lento"   # pula os testes de integração
python -m pytest tests/test_recorte.py -v
```

As imagens de teste são **geradas** por `tests/fixtures/gerar_fixtures.py` na
primeira execução — nada de binário no repositório. Para regerar:

```powershell
python tests/fixtures/gerar_fixtures.py
```

A fixture principal (`foto_grade_valida.png`) imita o que a câmera produz: fundo
amarelo, 3 quadrantes na horizontal, as duas linhas do quadrante central
inteiras e as linhas dos vizinhos cortadas pelo enquadramento — que é exatamente
o contraste de intensidade usado pelo algoritmo para escolher o par central.

### O que os testes travam

- os **parâmetros de calibração** e os *defaults* das funções migradas
  (comparados por `inspect.signature`);
- a **detecção**: 4 picos encontrados, crop entre as duas linhas centrais, altura
  preservada inteira no modo `so_vertical`;
- **crop em resolução cheia**: a saída é bit-a-bit igual a `original[y1:y2, x1:x2]`;
- **lossless**: PNG e TIFF relidos do disco batem exatamente com o array em memória;
- **layout do stitching**: `a1` no topo-esquerda, `d10` embaixo-direita, letras
  como linhas e números como colunas (blindagem contra placa transposta);
- **placeholder** amarelo nas células ausentes;
- **ordem natural** da renomeação (`img2` antes de `img10`) e cópia byte-a-byte;
- **ZIP** em modo `ZIP_STORED` e detecção de divergência de MD5;
- **robustez**: foto corrompida não derruba o lote; ordem dos resultados
  preservada no paralelismo.

---

## Preservação de qualidade

| Requisito | Como é garantido |
|---|---|
| Leitura em resolução cheia | `cv2.imread` sem downscale; a redução de 25% só existe em memória para detectar as linhas |
| Crop em resolução cheia | a detecção devolve **frações** (`crop_box_frac`), aplicadas na imagem original |
| Sem re-encoding intermediário | o crop é uma *slice* NumPy da imagem decodificada; nada é recomprimido antes de salvar |
| Saída lossless | PNG (`IMWRITE_PNG_COMPRESSION=1`) ou TIFF LZW (`IMWRITE_TIFF_COMPRESSION=5`) |
| Integridade na renomeação | `shutil.copy` + MD5 conferido em ambos os lados |
| ZIP sem recompressão | `zipfile.ZIP_STORED` + verificação de MD5 dos bytes dentro do ZIP |
| Sem upscale na exportação | resoluções maiores que a placa são puladas (registrado em DEBUG) |

Uma nota sobre `EXPORTACAO_RESOLUCOES`: se a placa montada tiver menos de
3840 px de largura, os perfis `10k` e `4k` são **ignorados** — o pipeline nunca
inventa pixel que não existe. A largura da placa é
`10 × largura_do_quadrante_recortado`.

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'config'`**
O ambiente virtual não está ativo, ou você chamou o script de um jeito que não
passa pela raiz do projeto. Ative com `.\.venv\Scripts\Activate.ps1` e rode a
partir da pasta do projeto. Alternativa definitiva: `pip install -e .`

**`cv2.imread` retorna `None` / "Imagem ilegivel"**
Três causas comuns: (1) arquivo corrompido no cartão — reveja a foto original;
(2) extensão que o OpenCV não abre (HEIC, RAW) — converta para JPEG/PNG antes;
(3) caminho com acentos. O terceiro caso já tem resgate automático via
`imread_fallback`, mas se o problema persistir, mova os dados para um caminho
sem acentos usando `YELLOWTRAP_DATA_DIR`.

**Muitos `Recortadas SEM deteccao`**
A grade não está sendo encontrada. Verifique iluminação e contraste — as linhas
pretas precisam ficar nitidamente mais escuras que o fundo amarelo. **Não mude
os parâmetros de calibração para "resolver"**: isso muda o recorte de todas as
fotos boas também. Prefira corrigir a captura. Se realmente for necessário
recalibrar, faça em ambiente de teste e rode a suíte comparando as placas.

**A placa saiu transposta / quadrantes fora de ordem**
As fotos não estavam na sequência `a1 → d10`. A renomeação usa a **ordem natural
dos nomes de arquivo**, então a garantia de ordem vem da captura. Confira em
`data/02_renomeadas/` se `a1.jpg` é mesmo o primeiro quadrante.

**`MemoryError` no stitching**
A placa completa é um array de `10 × largura × 4 × altura × 3` bytes. Com
quadrantes de 2000×3000 px isso passa de 1 GB. Opções: reduza
`EXPORTACAO_RESOLUCOES`, desligue `EXPORTACAO_INCLUIR_TIFF`, ou gere a prévia com
`run_apenas_stitching.py --escala 0.5` antes do arquivo final.

**Watcher não dispara**
Confira: (1) o grupo já tem 40 fotos com o mesmo prefixo? Nomes misturados
(`IMG_001` e `DSC0002`) formam grupos diferentes; (2) as fotos já foram
processadas antes? Veja `.watcher_state.json` — use `--forcar` para reprocessar;
(3) `watchdog` está instalado? Sem ele o watcher ainda funciona, mas só por
polling (avisa no log ao iniciar).

**Lote incompleto travado esperando para sempre**
É o comportamento configurado: nada é processado pela metade. Se quiser montar a
placa com o que existe, rode o modo batch manualmente — as células faltantes
viram placeholder amarelo.

**A barra de progresso aparece como "erro" no PowerShell**
O `tqdm` escreve na saída de erro padrão e o PowerShell marca isso como
`NativeCommandError`. É cosmético; o pipeline rodou normalmente. Para silenciar,
`PARALELISMO_CHUNK_TQDM = False` em `settings.py`.

**Está lento**
O default deixa 1 core livre. Em uma máquina dedicada, suba os workers:
`--workers 12`. Se o gargalo for disco (fotos grandes em rede), mais workers não
ajudam — copie o lote para o disco local antes.

---

## Integração com n8n / Railway

O pipeline já expõe os ganchos necessários para automação:

**Webhook de fim de lote** — defina a variável de ambiente e o sumário completo
é enviado por `POST` (JSON) ao final de cada execução:

```powershell
$env:YELLOWTRAP_WEBHOOK_URL = "https://SEU-N8N.exemplo.com/webhook/yellowtrap"
python scripts/watcher.py
```

O corpo do POST é o mesmo conteúdo do `sumario.json` (lote, contagens, falhas,
arquivos gerados, duração). Usa só a stdlib — sem dependência extra.

**Execução agendada** — `python scripts/watcher.py --uma-vez` roda um ciclo e sai
com código `0`/`1`, adequado para Agendador de Tarefas do Windows, cron ou um nó
*Execute Command* do n8n.

**Próximo passo natural** — a pasta `data/03_recortadas/` é exatamente a entrada
que o modelo de deep learning espera: 40 quadrantes limpos, nomeados por posição
no grid. A inferência pode ler dessa pasta (ou consumir o `sumario.json` para
saber quais quadrantes são confiáveis) sem tocar em nada deste repositório.

---

*Grupo Progresso — Setor de Inovação · versão 1.0.0*
