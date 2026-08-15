"""
Configuracao centralizada do YellowTrap Pipeline.

TODOS os parametros ajustaveis do sistema vivem aqui. Nenhum outro modulo
deve conter numeros magicos.

AVISO IMPORTANTE
----------------
Os blocos marcados com "VALIDADO NO COLAB - NAO ALTERAR" contem parametros
calibrados atraves de dezenas de iteracoes em producao. Alterar qualquer um
deles muda o resultado do recorte e invalida a calibracao.

O pipeline termina no RECORTE. A montagem da placa (stitching) foi removida:
a entrega final sao os quadrantes recortados, prontos para o proximo estagio
do sistema (inferencia / armazenamento).
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Raiz do projeto
# ---------------------------------------------------------------------------
# Ancorado no arquivo (e nao no cwd) para que os scripts funcionem sendo
# chamados de qualquer diretorio.
BASE_DIR = Path(__file__).resolve().parent.parent

# Permite apontar os dados para outro disco/volume sem editar este arquivo:
#   set YELLOWTRAP_DATA_DIR=D:\yellowtrap\data
PASTA_DADOS = Path(os.environ.get("YELLOWTRAP_DATA_DIR", BASE_DIR / "data"))

# ---------------------------------------------------------------------------
# Estrutura de pastas
# ---------------------------------------------------------------------------
PASTA_ENTRADA = PASTA_DADOS / "01_entrada_bruta"
PASTA_RENOMEADAS = PASTA_DADOS / "02_renomeadas"
PASTA_RECORTADAS = PASTA_DADOS / "03_recortadas"
PASTA_RELATORIOS = PASTA_DADOS / "_relatorios"
PASTA_FALHAS = PASTA_DADOS / "_falhas"
PASTA_ZIPS = PASTA_DADOS / "_zips"

PASTA_LOGS = Path(os.environ.get("YELLOWTRAP_LOG_DIR", BASE_DIR / "logs"))
ARQUIVO_LOG = PASTA_LOGS / "pipeline.log"

# Lista usada pelo scripts/setup_inicial.py
PASTAS_OBRIGATORIAS = [
    PASTA_ENTRADA,
    PASTA_RENOMEADAS,
    PASTA_RECORTADAS,
    PASTA_RELATORIOS,
    PASTA_FALHAS,
    PASTA_ZIPS,
    PASTA_LOGS,
]

# ---------------------------------------------------------------------------
# Modos de processamento
# ---------------------------------------------------------------------------
# O mesmo motor atende tres formas de operacao. Quem chama escolhe o modo -
# nada alem do NOME dos arquivos muda entre eles; o recorte e identico.
#
#   grid       -> comportamento historico: renomeia para o grid da placa
#                 (a1..d10, ate QUANTIDADE_ESPERADA fotos) e recorta.
#   sequencial -> renomeia para VARD0000001, VARD0000002, ... sem limite de
#                 quantidade, e recorta. Modo indicado para lotes grandes.
#   recorte    -> nao renomeia nada: recorta preservando o nome de origem.
MODO_GRID = "grid"
MODO_SEQUENCIAL = "sequencial"
MODO_RECORTE = "recorte"
MODOS_VALIDOS = (MODO_GRID, MODO_SEQUENCIAL, MODO_RECORTE)
MODO_PADRAO = MODO_GRID

# --- Modo sequencial (VARD0000001) ---
SEQUENCIAL_PREFIXO = "VARD"
SEQUENCIAL_DIGITOS = 7  # VARD0000001 .. VARD9999999
SEQUENCIAL_INICIO = 1
# True  -> a numeracao continua de onde parou (le o maior indice ja existente
#          na pasta de saida). Essencial quando o sistema recebe varios envios
#          que precisam entrar no mesmo acervo sem colidir.
# False -> cada execucao recomeca em SEQUENCIAL_INICIO.
SEQUENCIAL_CONTINUAR_NUMERACAO = False

# ---------------------------------------------------------------------------
# Grid da placa YellowTrap (usado apenas pelo modo 'grid')
# ---------------------------------------------------------------------------
LETRAS_COLUNAS = ["a", "b", "c", "d"]
NUMEROS_LINHAS = list(range(1, 11))
QUANTIDADE_ESPERADA = 40

EXTENSOES_IMAGEM = {".jpg", ".jpeg", ".png", ".bmp", ".tiff"}

# ---------------------------------------------------------------------------
# Protocolo 1 - Renomeacao
# ---------------------------------------------------------------------------
# COMO o nome novo e materializado antes do recorte:
#
#   virtual  -> nada e gravado em 02_renomeadas. O nome novo e aplicado
#               direto no arquivo do recorte. E o mais rapido e o unico que
#               nao duplica o lote em disco - obrigatorio em lotes grandes.
#   hardlink -> cria um hardlink em 02_renomeadas (instantaneo, sem espaco
#               extra). Cai para 'copiar' se o sistema de arquivos recusar.
#   copiar   -> copia byte-a-byte (comportamento historico do Colab).
#   mover    -> move o arquivo (esvazia a pasta de entrada).
ESTRATEGIA_VIRTUAL = "virtual"
ESTRATEGIA_HARDLINK = "hardlink"
ESTRATEGIA_COPIAR = "copiar"
ESTRATEGIA_MOVER = "mover"
ESTRATEGIAS_VALIDAS = (ESTRATEGIA_VIRTUAL, ESTRATEGIA_HARDLINK,
                       ESTRATEGIA_COPIAR, ESTRATEGIA_MOVER)

# Default por modo. O modo grid mantem a copia com MD5 (lote de 40 fotos, o
# custo e irrelevante e a rastreabilidade e util); o sequencial, pensado para
# milhares de fotos, nao materializa nada.
RENOMEACAO_ESTRATEGIA = {
    MODO_GRID: ESTRATEGIA_COPIAR,
    MODO_SEQUENCIAL: ESTRATEGIA_VIRTUAL,
    MODO_RECORTE: ESTRATEGIA_VIRTUAL,
}

# Conferencia MD5 origem<->destino. So se aplica a estrategia 'copiar'
# (hardlink e mover nao reescrevem bytes; virtual nao copia nada).
# Custa 2 leituras completas do lote - desligue em lotes muito grandes.
RENOMEACAO_VERIFICAR_MD5 = True
RENOMEACAO_CHUNK_MD5 = 1024 * 1024  # 1 MB por leitura (era 8 KB no Colab)

# A materializacao e I/O-bound: threads dao ganho real e nao disputam CPU com
# o recorte. None = min(8, cpu_count * 2).
RENOMEACAO_WORKERS_IO = None

# O ZIP existia no Colab para download com bytes preservados. Localmente ele
# duplica o espaco em disco, entao vem desligado.
RENOMEACAO_CRIAR_ZIP = False
RENOMEACAO_VERIFICAR_ZIP = True  # so tem efeito se RENOMEACAO_CRIAR_ZIP = True

# Esvazia a pasta de destino antes de renomear (comportamento do Colab).
RENOMEACAO_LIMPAR_DESTINO = True

# ---------------------------------------------------------------------------
# Protocolo 2 - Recorte  |  VALIDADO NO COLAB - NAO ALTERAR
# ---------------------------------------------------------------------------
RECORTE_FATOR_DETECCAO = 0.25
RECORTE_MARGEM = 8
RECORTE_MODO = "so_vertical"
RECORTE_SPAN_V_INICIAL_FRAC = 0.5
RECORTE_SPAN_H_INICIAL_FRAC = 0.5
RECORTE_SPAN_MINIMO_FRAC = 0.15
RECORTE_DILATAR = True
RECORTE_FORMATO_SAIDA = "png"  # 'png' | 'tiff' | 'jpg_max'
# Espelhado em src/exportacao.py (_EXTENSOES) - ha teste conferindo os dois.
RECORTE_FORMATOS_VALIDOS = ("png", "tiff", "jpg_max")

# Parametros internos do detector de linhas (tambem validados no Colab).
RECORTE_LIMIAR_FRAC = 0.25
RECORTE_MIN_DIST = 30

# Quando a deteccao das linhas falha, o comportamento validado no Colab e
# devolver a imagem CHEIA sem recorte. Mantido como default.
#   False -> salva a imagem cheia, loga WARNING e grava o JSON de diagnostico
#            em data/_falhas/ (o arquivo NAO e movido).
#   True  -> trata como falha dura: nao gera o quadrante e move a foto para
#            data/_falhas/.
RECORTE_FALHA_DETECCAO_E_ERRO = False

# Grava um JSON de diagnostico por foto sem deteccao. Em lotes de milhares de
# fotos isso pode gerar milhares de arquivinhos - desligue se a contagem do
# sumario ja for suficiente.
RECORTE_REGISTRAR_JSON_SEM_DETECCAO = True

# Retomada: se o quadrante de destino ja existe, a foto e pulada. Deixa
# reprocessar um lote interrompido custar apenas o que faltava.
RECORTE_PULAR_EXISTENTES = False

# Esvazia a pasta de recortes antes do lote. Ficou DESLIGADO por padrao: sem
# o stitching nao ha mais risco de misturar lotes na placa, e apagar milhares
# de arquivos a cada execucao e caro e incompativel com a retomada.
LIMPAR_PASTAS_INTERMEDIARIAS = False

# ---------------------------------------------------------------------------
# Paralelismo
# ---------------------------------------------------------------------------
NUM_WORKERS = None  # None = os.cpu_count() - 1
PARALELISMO_CHUNK_TQDM = True  # barra de progresso no terminal
# Threads internas do OpenCV DENTRO de cada worker. 1 evita oversubscription
# (N processos x N threads) e nao altera o resultado dos algoritmos.
PARALELISMO_CV2_THREADS = 1

# Quantas tarefas ficam submetidas por worker ao mesmo tempo. Limita a fila em
# memoria: com 50.000 fotos nao existem 50.000 futures vivos, e sim
# workers x este fator. 2 a 4 mantem os workers sempre alimentados.
PARALELISMO_JANELA_POR_WORKER = 4

# Nivel minimo dos logs emitidos DENTRO dos workers. Cada registro atravessa
# uma fila entre processos; em lotes grandes, DEBUG vira gargalo.
PARALELISMO_NIVEL_LOG_WORKERS = "INFO"

# Recicla o processo worker a cada N tarefas (Python >= 3.11). Protege contra
# crescimento de memoria em lotes muito longos. None = nunca recicla (mais
# rapido: no Windows cada respawn reimporta o OpenCV).
PARALELISMO_TAREFAS_POR_WORKER = None

# A partir de quantos itens o resultado de cada foto deixa de ser acumulado em
# memoria (a agregacao passa a ser incremental, por callback).
PARALELISMO_LIMIAR_STREAMING = 500

# ---------------------------------------------------------------------------
# Sumario
# ---------------------------------------------------------------------------
# Quantas falhas individuais entram no sumario/JSON. O contador total continua
# exato; o detalhamento e limitado para o relatorio nao virar um arquivo de
# centenas de MB num lote gigante que deu errado.
SUMARIO_MAX_FALHAS_DETALHADAS = 200

# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------
WATCHER_INTERVALO_POLL_SEG = 5
WATCHER_TIMEOUT_LOTE_INCOMPLETO_SEG = 30
# No modo grid, lote incompleto NUNCA e processado automaticamente: o watcher
# espera indefinidamente pelos arquivos faltantes. No modo sequencial nao ha
# "tamanho certo" de lote - o watcher fecha o lote depois de
# WATCHER_TIMEOUT_LOTE_INCOMPLETO_SEG segundos sem chegar arquivo novo.
WATCHER_ARQUIVO_ESTADO = BASE_DIR / ".watcher_state.json"
# Ao fechar um lote, as fotos sao movidas de 01_entrada_bruta para
# 01_entrada_bruta/_lotes/<lote_id>/. Isso mantem a pasta de entrada limpa e
# impede que as fotos do lote anterior entrem no mapeamento do proximo.
# False = as fotos sao COPIADAS (a pasta de entrada precisa ser limpa a mao).
WATCHER_ARQUIVAR_ENTRADA = True
PASTA_LOTES_ARQUIVADOS = PASTA_ENTRADA / "_lotes"
# Numero de ciclos consecutivos com tamanho de arquivo estavel antes de
# considerar que a copia terminou (evita processar arquivo pela metade).
WATCHER_CICLOS_ESTABILIDADE = 2

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_NIVEL_ARQUIVO = "DEBUG"
LOG_NIVEL_CONSOLE = "INFO"
LOG_FORMATO = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
LOG_FORMATO_DATA = "%Y-%m-%d %H:%M:%S"
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
LOG_BACKUP_COUNT = 5

# ---------------------------------------------------------------------------
# Falhas
# ---------------------------------------------------------------------------
# True  -> a foto que falhou e COPIADA para _falhas (original permanece).
# False -> a foto e MOVIDA para _falhas.
FALHAS_COPIAR_EM_VEZ_DE_MOVER = False

# ---------------------------------------------------------------------------
# Notificacao (opcional - integracao n8n)
# ---------------------------------------------------------------------------
WEBHOOK_ATIVO = bool(os.environ.get("YELLOWTRAP_WEBHOOK_URL"))
WEBHOOK_URL = os.environ.get("YELLOWTRAP_WEBHOOK_URL", "")
WEBHOOK_TIMEOUT_SEG = 10
