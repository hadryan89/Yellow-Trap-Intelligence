"""
Configuracao centralizada do YellowTrap Pipeline.

TODOS os parametros ajustaveis do sistema vivem aqui. Nenhum outro modulo
deve conter numeros magicos.

AVISO IMPORTANTE
----------------
Os blocos marcados com "VALIDADO NO COLAB - NAO ALTERAR" contem parametros
calibrados atraves de dezenas de iteracoes em producao. Alterar qualquer um
deles muda o resultado do recorte/stitching e invalida a calibracao.
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
PASTA_PLACAS = PASTA_DADOS / "04_placas_montadas"
PASTA_FALHAS = PASTA_DADOS / "_falhas"
PASTA_ZIPS = PASTA_DADOS / "_zips"

PASTA_LOGS = Path(os.environ.get("YELLOWTRAP_LOG_DIR", BASE_DIR / "logs"))
ARQUIVO_LOG = PASTA_LOGS / "pipeline.log"

# Lista usada pelo scripts/setup_inicial.py
PASTAS_OBRIGATORIAS = [
    PASTA_ENTRADA,
    PASTA_RENOMEADAS,
    PASTA_RECORTADAS,
    PASTA_PLACAS,
    PASTA_FALHAS,
    PASTA_ZIPS,
    PASTA_LOGS,
]

# ---------------------------------------------------------------------------
# Grid da placa YellowTrap
# ---------------------------------------------------------------------------
# ATENCAO ao nome historico: no layout HORIZONTAL de montagem, cada LETRA vira
# uma faixa horizontal (uma linha da placa) e cada NUMERO vira uma coluna.
# Resultado final: 4 linhas (a,b,c,d) x 10 colunas (1..10).
LETRAS_COLUNAS = ["a", "b", "c", "d"]
NUMEROS_LINHAS = list(range(1, 11))
QUANTIDADE_ESPERADA = 40

EXTENSOES_IMAGEM = {".jpg", ".jpeg", ".png", ".bmp", ".tiff"}

# ---------------------------------------------------------------------------
# Protocolo 1 - Renomeacao
# ---------------------------------------------------------------------------
# O ZIP existia no Colab para download com bytes preservados. Localmente ele
# duplica o espaco em disco (~40 fotos por lote), entao vem desligado.
# A verificacao MD5 copia<->origem roda SEMPRE, independente desta flag.
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

# Parametros internos do detector de linhas (tambem validados no Colab).
RECORTE_LIMIAR_FRAC = 0.25
RECORTE_MIN_DIST = 30

# Quando a deteccao das linhas falha, o comportamento validado no Colab e
# devolver a imagem CHEIA sem recorte. Mantido como default.
#   False -> salva a imagem cheia, loga WARNING e grava o JSON de diagnostico
#            em data/_falhas/ (o arquivo NAO e movido).
#   True  -> trata como falha dura: nao gera o quadrante, move a foto para
#            data/_falhas/ e o stitching preenche a celula com o placeholder.
RECORTE_FALHA_DETECCAO_E_ERRO = False

# Limpa 03_recortadas antes de cada lote (evita que quadrantes de um lote
# anterior entrem no stitching do lote atual).
LIMPAR_PASTAS_INTERMEDIARIAS = True

# ---------------------------------------------------------------------------
# Protocolo 3 - Stitching  |  VALIDADO NO COLAB - NAO ALTERAR
# ---------------------------------------------------------------------------
STITCHING_ESCALA_CARREGAMENTO = 1.0  # resolucao cheia
STITCHING_COR_PLACEHOLDER = (40, 230, 250)  # amarelo (BGR)
STITCHING_PADRAO_NOME = r"([A-Za-z])(\d+)"

# ---------------------------------------------------------------------------
# Exportacao (multiplas resolucoes da placa final)
# ---------------------------------------------------------------------------
EXPORTACAO_RESOLUCOES = [
    ("10k", 10000, 92),
    ("4k", 3840, 92),
    ("1200p", 1200, 90),
    ("720p", 720, 88),
]
EXPORTACAO_INCLUIR_PNG_LOSSLESS = True
EXPORTACAO_INCLUIR_TIFF = True
EXPORTACAO_INCLUIR_WEBP = True

# Compressoes usadas nos salvamentos lossless (constantes do OpenCV).
PNG_COMPRESSION = 1  # 0-9; 1 = rapido e lossless
TIFF_COMPRESSION = 5  # 5 = LZW
WEBP_QUALITY = 95

# ---------------------------------------------------------------------------
# Paralelismo
# ---------------------------------------------------------------------------
NUM_WORKERS = None  # None = os.cpu_count() - 1
PARALELISMO_CHUNK_TQDM = True  # barra de progresso no terminal
# Threads internas do OpenCV DENTRO de cada worker. 1 evita oversubscription
# (N processos x N threads) e nao altera o resultado dos algoritmos.
PARALELISMO_CV2_THREADS = 1

# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------
WATCHER_INTERVALO_POLL_SEG = 5
WATCHER_TIMEOUT_LOTE_INCOMPLETO_SEG = 30
# Lote incompleto NUNCA e processado automaticamente: o watcher espera
# indefinidamente pelos arquivos faltantes, logando um WARNING a cada
# WATCHER_TIMEOUT_LOTE_INCOMPLETO_SEG segundos de inatividade.
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
