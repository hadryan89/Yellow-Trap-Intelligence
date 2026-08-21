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
#                 AMARELA (a1..d10, ate QUANTIDADE_ESPERADA fotos) e recorta.
#                 A planta da placa AZUL nao esta mapeada aqui - lote azul
#                 usa 'sequencial' ou 'recorte'. Isso e so nomeacao: o
#                 recorte e o mesmo nos tres modos, em qualquer cor.
#   sequencial -> renomeia para VARD1, VARD2, VARD3, ... sem limite de
#                 quantidade, e recorta. Modo indicado para lotes grandes
#                 e para armadilha azul.
#   recorte    -> nao renomeia nada: recorta preservando o nome de origem.
MODO_GRID = "grid"
MODO_SEQUENCIAL = "sequencial"
MODO_RECORTE = "recorte"
MODOS_VALIDOS = (MODO_GRID, MODO_SEQUENCIAL, MODO_RECORTE)
MODO_PADRAO = MODO_GRID

# --- Modo sequencial (VARD1, VARD2, VARD3, ...) ---
SEQUENCIAL_PREFIXO = "VARD"
# Largura MINIMA do contador, preenchida com zeros a esquerda.
#   1 -> VARD1, VARD2, ... VARD10, VARD100  (sem zeros; e o padrao)
#   7 -> VARD0000001, VARD0000002, ...      (nomes de largura fixa)
# Com 1, o contador nunca e truncado: passando de 9 o nome simplesmente
# cresce (VARD10). A ordenacao do pipeline e natural, entao VARD2 continua
# vindo antes de VARD10 - so a ordenacao alfabetica pura (de alguns
# programas externos) e que embaralharia. Se isso importar no seu fluxo,
# use uma largura fixa aqui.
SEQUENCIAL_DIGITOS = 1
# A contagem comeca em 1: o primeiro quadrante do lote e VARD1, nao VARD0.
SEQUENCIAL_INICIO = 1
# True  -> a numeracao continua de onde parou (le o maior indice ja existente
#          na pasta de saida). Essencial quando o sistema recebe varios envios
#          que precisam entrar no mesmo acervo sem colidir.
# False -> cada execucao recomeca em SEQUENCIAL_INICIO.
SEQUENCIAL_CONTINUAR_NUMERACAO = False

# ---------------------------------------------------------------------------
# Grid da placa AMARELA (usado apenas pelo modo 'grid')
#
# 4 colunas x 10 linhas = 40 posicoes. E a planta da placa amarela, nao uma
# propriedade do recorte. Nao existe grid equivalente para a azul: aquele
# lote entra por 'sequencial'.
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
#               nao duplica o lote em disco - PADRAO de todos os modos.
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

# Default de TODOS os modos: 'virtual'. Uma foto que entra gera UM arquivo
# novo - o quadrante recortado, ja com o nome final. A pasta 02_renomeadas
# so e escrita se alguem pedir explicitamente (--materializar copiar, por
# exemplo), porque qualquer estrategia diferente de 'virtual' duplica o lote
# inteiro em disco: 30 fotos de entrada viravam 30 copias em 02_renomeadas
# + 30 quadrantes em 03_recortadas.
RENOMEACAO_ESTRATEGIA_PADRAO = ESTRATEGIA_VIRTUAL
RENOMEACAO_ESTRATEGIA = {
    MODO_GRID: ESTRATEGIA_VIRTUAL,
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
# Protocolo 2 - Recorte  |  CALIBRACAO - ver src/recorte.py antes de mexer
# ---------------------------------------------------------------------------
# Estes valores foram medidos sobre os dois acervos reais (65 fotos de
# armadilha AZUL e 40 de AMARELA). A referencia de "esta calibrado" e o
# desvio-padrao da largura do quadrante entregue: com estes numeros ele fica
# abaixo de 1% em cada cor. Alterou algo aqui? rode
# `pytest tests/test_recorte_acervo.py` antes de subir.

# A deteccao roda numa copia reduzida da foto. 0.125 sobre 9600x5400 da uma
# imagem de 1200x675 - resolucao de sobra para linha de grade, e ~4x mais
# barato que 0.25. Os parametros abaixo sao todos FRACIONARIOS justamente
# para acompanhar este fator, mas a calibracao foi MEDIDA em 0.125: mudou o
# fator, revalide com `pytest tests/test_recorte_acervo.py`.
RECORTE_FATOR_DETECCAO = 0.125
# Piso de largura da copia de deteccao, em pixels. Numa foto menor que a do
# microscopio o fator afrouxa sozinho ate chegar aqui - senao a linha da
# grade ficaria com menos de um pixel e a deteccao falharia sem motivo.
# 9600 x 0.125 = 1200, entao o piso nao mexe no caso de producao.
RECORTE_LARGURA_MINIMA_DETECCAO = 900

# Folga, em fracao da largura, contada a partir da borda da linha da grade.
# O SENTIDO dela depende de RECORTE_BORDA (abaixo): em 'dentro' ela empurra o
# corte para dentro do quadrante (nenhum pedaco de linha entra); em 'linha'
# ela empurra para fora (nenhum pedaco de linha fica de fora). Nos dois casos
# a folga ainda cresce com a inclinacao medida, porque a linha e reta no meio
# da foto mas deriva nas pontas.
RECORTE_MARGEM_FRAC = 0.004

# O que fazer com a LINHA da grade - o traco impresso que separa dois
# quadrantes e pertence aos dois.
#
#   dentro     -> corta pela borda de DENTRO da linha: o quadrante sai sem
#                 nenhum traco. Era o unico comportamento ate a v2.0.
#   linha      -> corta pela borda de FORA: a linha aparece INTEIRA nos quatro
#                 lados do quadrante. E o PADRAO, porque a etapa seguinte
#                 (juntar os 40 quadrantes de volta na placa) precisa dos
#                 tracos para remontar a grade.
#   meia_linha -> corta pelo MEIO da linha. Cada quadrante leva metade dela,
#                 entao ao encostar dois vizinhos a linha se reconstitui com a
#                 espessura ORIGINAL. Em 'linha' a mesma emenda sai com a
#                 linha dobrada (cada lado trouxe a sua copia inteira) - use
#                 'meia_linha' se a montagem precisar ser fiel ao papel.
#
# O que muda e so onde o corte cai; a deteccao e identica nos tres. Incluir a
# linha custa uma tira do quadrante vizinho junto (a espessura da linha mais a
# folga), inevitavel num recorte alinhado aos eixos quando a grade esta torta.
# Ate onde vai a linha, medido no BRILHO. A mascara do blackhat marca o
# NUCLEO do traco: a beirada impressa desbota e nao responde ao realce, entao
# cortar pela mascara entrega meia linha. A linha acaba onde o brilho ja
# voltou (1 - este fator) do caminho ate o papel - 0.25 = 75% recuperado.
# Menor = corte mais colado ao nucleo (arrisca cortar traco); maior = corte
# mais folgado (arrisca sobrar papel).
RECORTE_RECUPERACAO_FRAC = 0.25

# Quantas vezes mais larga que a parceira uma linha pode ser antes de a
# medida dela ser descartada. Acima disso nao e traco mais grosso: e traco
# com coisa escura colada (a moldura da foto, uma sombra), e quem manda
# passa a ser a largura da parceira.
RECORTE_LINHA_INFLADA = 2.0

RECORTE_BORDA_DENTRO = "dentro"
RECORTE_BORDA_LINHA = "linha"
RECORTE_BORDA_MEIA_LINHA = "meia_linha"
RECORTE_BORDAS_VALIDAS = (RECORTE_BORDA_DENTRO, RECORTE_BORDA_LINHA,
                          RECORTE_BORDA_MEIA_LINHA)
RECORTE_BORDA = RECORTE_BORDA_LINHA

# Em quais eixos recortar.
#   ambos       -> recorta os 4 lados no quadrante. A celula da grade e
#                  menor que a altura da foto (9600x5400 para celula de
#                  ~4400), entao sem isto a linha horizontal da grade e as
#                  tiras dos quadrantes de cima e de baixo ficam dentro da
#                  entrega. E o padrao.
#   so_vertical -> recorta so as laterais e devolve a altura inteira da foto
#                  (comportamento historico). Util se o proximo estagio ja
#                  contava com a altura cheia.
# Em 'ambos', quando o eixo vertical nao pode ser determinado (nenhuma linha
# horizontal no enquadramento), a altura cheia e mantida - nunca piora.
RECORTE_EIXO_AMBOS = "ambos"
RECORTE_EIXO_SO_VERTICAL = "so_vertical"
RECORTE_EIXOS_VALIDOS = (RECORTE_EIXO_AMBOS, RECORTE_EIXO_SO_VERTICAL)
RECORTE_EIXOS = RECORTE_EIXO_AMBOS

RECORTE_FORMATO_SAIDA = "png"  # 'png' | 'tiff' | 'jpg_max'
# Espelhado em src/exportacao.py (_EXTENSOES) - ha teste conferindo os dois.
RECORTE_FORMATOS_VALIDOS = ("png", "tiff", "jpg_max")

# --- Perfis de armadilha ---------------------------------------------------
# O ALGORITMO E O MESMO para todas as cores: o detector nao olha matiz, olha
# geometria. O perfil so aperta a faixa de largura aceita para o quadrante,
# e serve de trava extra quando um lote e dificil (muita oclusao, foto
# tremida). Medido nos acervos de referencia com esta calibracao (65 fotos
# azuis, 40 amarelas - as 2 amarelas que nao sao armadilha ficam de fora):
#   azul     largura 0.446-0.471 da foto (mediana 0.457, desvio 0.005)
#   amarela  largura 0.518-0.554 da foto (mediana 0.535, desvio 0.009)
# As faixas abaixo sao mais largas que o medido de proposito - elas sao
# trava contra absurdo, nao o intervalo esperado.
#
#   auto     -> aceita as duas (e qualquer armadilha nova na mesma faixa)
#   amarela  -> so aceita quadrante de armadilha amarela
#   azul     -> so aceita quadrante de armadilha azul
RECORTE_PERFIL_PADRAO = "auto"
RECORTE_PERFIS = {
    "auto":    {"largura_min_frac": 0.30, "largura_max_frac": 0.68},
    "amarela": {"largura_min_frac": 0.45, "largura_max_frac": 0.65},
    "azul":    {"largura_min_frac": 0.38, "largura_max_frac": 0.56},
}

# --- Realce das linhas (blackhat) ------------------------------------------
# Largura maxima de uma linha da grade, em fracao da largura da foto. O
# kernel do blackhat precisa ser MAIOR que a linha (para ela responder) e
# MENOR que qualquer mancha escura larga (para ela NAO responder).
RECORTE_ESPESSURA_MAX_FRAC = 0.04
# Limiar de binarizacao do blackhat: metade do percentil 99 da resposta, com
# um piso absoluto para fotos praticamente vazias (onde o percentil colapsa).
RECORTE_BLACKHAT_PERCENTIL = 99.0
RECORTE_BLACKHAT_FATOR = 0.5
RECORTE_BLACKHAT_PISO = 10

# --- Correcao de inclinacao ------------------------------------------------
# Giro maximo procurado, em graus. As fotos reais ficam entre -2 e +2; 3 da
# margem sem deixar a busca cara nem ambigua.
RECORTE_INCLINACAO_MAX_GRAUS = 3.0

# --- Perfil por coluna -----------------------------------------------------
# Falha de ate 4% da altura dentro da linha e costurada antes de medir (uma
# praga em cima da linha, um trecho de impressao apagado).
RECORTE_PONTE_FRAC = 0.04
# Duas colunas mais proximas que isto (fracao da largura) sao o MESMO pico.
RECORTE_DISTANCIA_MIN_FRAC = 0.02
# Niveis (altura_minima_da_linha, cobertura_minima_da_coluna), do mais duro
# ao mais frouxo. Para no primeiro que ja produz um par valido - assim uma
# foto limpa nunca paga o preco (em falsos positivos) de uma foto ocluida.
RECORTE_NIVEIS = (
    (0.55, 0.50),
    (0.40, 0.40),
    (0.28, 0.30),
    (0.18, 0.22),
)
# Um pico com menos que esta fracao da forca do melhor e sujeira, nao linha.
RECORTE_FORCA_RELATIVA = 0.55
# E, em absoluto, uma borda de quadrante precisa cobrir pelo menos esta
# fracao da altura da foto. Medido no acervo, o par escolhido nunca ficou
# abaixo de 0.48 - 0.35 da folga e ainda barra foto fora de foco, onde os
# 'picos' nao passam de 0.29.
RECORTE_FORCA_MINIMA = 0.35
# Quando UMA das linhas esta ocluida, o par e deduzido da outra mais o
# passo da grade. A linha que serve de ancora nesse caso precisa ser mais
# convincente que uma borda comum - o recorte inteiro sai dela.
RECORTE_FORCA_ANCORA = 0.55

# --- Aparo da moldura ------------------------------------------------------
# As faixas de borda que nao sao papel da armadilha (fundo do microscopio,
# sombra da moldura) saem do quadrante: sao pretas e so atrapalham a
# inferencia seguinte.
RECORTE_CORTAR_MOLDURA = True
# O alvo e a faixa PRETA (lateral da placa, fundo do microscopio, sombra):
# pixel mais escuro que isto nao e papel de armadilha nem conteudo util.
# O criterio e brilho e nao cor de propria: uma folha caida sobre a borda
# tambem nao parece papel, mas e conteudo do quadrante e nao pode ser cortada.
RECORTE_MOLDURA_BRILHO_MAX = 45
# So apara linha/coluna com pelo menos esta fracao de pixel escuro. Em 0.85 a
# faixa preta sai inteira e uma sombra parcial na borda fica.
RECORTE_MOLDURA_FRACAO_MIN = 0.85
# E nunca apara mais que esta fracao de cada ponta.
RECORTE_MOLDURA_CORTE_MAX_FRAC = 0.15

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
