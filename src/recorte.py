"""
Protocolo 2 - Recorte do quadrante central.

Cada foto individual captura o quadrante alvo mais pedacos dos vizinhos
(limitacao do microscopio). O algoritmo detecta as linhas da grade impressa
na armadilha e recorta apenas o quadrante que contem o centro da foto.

FUNCIONA COM QUALQUER COR DE ARMADILHA
--------------------------------------
A deteccao NAO depende da cor do fundo (amarelo, azul, ...). Em vez de
binarizar o cinza - que confunde "linha da grade" com "fundo escuro" e
quebra na armadilha azul, cujo fundo ja e escuro em tons de cinza - o
detector trabalha em quatro passos:

  1. BLACKHAT no canal V (brilho) com kernel HORIZONTAL. Isso realca apenas
     faixas escuras ESTREITAS na horizontal, ou seja, linhas verticais.
     Regioes escuras largas (moldura fora da armadilha, sombra, uma folha
     caida) nao respondem - era exatamente isso que envenenava o azul.
  2. CORRECAO DE INCLINACAO. As linhas raramente saem perfeitamente
     verticais; 1 grau de giro ja espalha a linha por dezenas de colunas e
     destroi a projecao. Uma busca tipo Radon acha o cisalhamento que deixa
     o perfil mais nitido.
  3. PERFIL POR COLUNA com criterio ABSOLUTO: vale como linha da grade a
     coluna coberta por >= X% da altura da foto (nao "X% do pico mais
     forte"). A exigencia e relaxada em niveis ate aparecer um par valido.
  4. ESCOLHA GEOMETRICA do par: entre linhas CONSECUTIVAS, fica o par cuja
     largura e plausivel para um quadrante e cujo meio esta mais perto do
     centro da foto. Nunca mais "a linha mais escura ganha" - era assim que
     o recorte pulava uma linha e entregava dois quadrantes colados.

O recorte sai nos QUATRO lados. A celula da grade e menor que a altura da
foto (9600x5400 para celula de ~4400), entao recortar so as laterais - o
comportamento antigo - deixava a linha horizontal da grade e tiras dos
quadrantes de cima e de baixo dentro da entrega. O eixo vertical passa pelo
MESMO detector, com a imagem transposta.

A LINHA FICA NO QUADRANTE
-------------------------
Achar as linhas e uma coisa; decidir de que lado delas o corte cai e outra.
Por default (RECORTE_BORDA = 'linha') o corte para NA beirada de fora de
cada linha: o quadrante vem com os quatro tracos da grade desenhados na
borda e NADA depois deles - o traco e a propria beirada do arquivo. E o que
permite encostar os 40 quadrantes de volta e enxergar a grade. 'dentro'
devolve o comportamento antigo (nenhum traco) e 'meia_linha' leva metade da
linha de cada lado, para que a emenda entre dois vizinhos reconstitua a
espessura original.

Quando uma das linhas esta ocluida (uma folha por cima, sujeira), o passo da
grade e emprestado do outro eixo: a celula e proxima de quadrada, entao a
altura dela serve de estimativa para a largura do quadrante e vice-versa. E
so estimativa - cada eixo mede o proprio par sempre que consegue, porque a
celula nao e exatamente quadrada em toda a armadilha.

QUALIDADE
---------
A deteccao roda sobre uma copia REDUZIDA em memoria por performance, mas o
crop e aplicado na imagem ORIGINAL em resolucao cheia atraves de coordenadas
fracionais. Nenhum pixel entregue passa por redimensionamento ou re-encoding
lossy.
"""

from __future__ import annotations

import gc
import time
from pathlib import Path

import cv2
import numpy as np

from config import settings
from src.exportacao import extensao_do_formato, salvar_recortada
from src.utils import imread_fallback, obter_logger, registrar_falha

logger = obter_logger(__name__)

__all__ = [
    "detectar_crop_box",
    "recortar_em_resolucao_cheia",
    "resolver_borda",
    "resolver_perfil",
    "salvar_recortada",
    "processar_foto",
    "processar_item",
]


def resolver_perfil(perfil: str | None = None) -> dict:
    """Faixa de largura esperada do quadrante, por cor de armadilha."""
    nome = str(perfil or settings.RECORTE_PERFIL_PADRAO).strip().lower()
    if nome not in settings.RECORTE_PERFIS:
        raise ValueError(
            f"Perfil de armadilha invalido: {nome!r}. "
            f"Validos: {', '.join(settings.RECORTE_PERFIS)}"
        )
    return {"nome": nome, **settings.RECORTE_PERFIS[nome]}


def resolver_borda(borda: str | None = None) -> str:
    """
    De que lado da linha da grade o corte cai.

    'linha' (default) entrega o traco inteiro nos quatro lados, 'dentro'
    entrega o quadrante sem traco nenhum e 'meia_linha' parte a linha ao
    meio entre os dois quadrantes vizinhos. Ver RECORTE_BORDA.
    """
    nome = str(borda or settings.RECORTE_BORDA).strip().lower()
    if nome not in settings.RECORTE_BORDAS_VALIDAS:
        raise ValueError(
            f"Borda invalida: {nome!r}. "
            f"Validas: {', '.join(settings.RECORTE_BORDAS_VALIDAS)}"
        )
    return nome


# ---------------------------------------------------------------------------
# 1. Mapa das linhas da grade (independente da cor do fundo)
# ---------------------------------------------------------------------------


def _impar(valor, minimo: int) -> int:
    """Tamanho de kernel: inteiro impar, nunca abaixo de `minimo`."""
    return max(minimo, int(valor) | 1)


def _mascara_linhas(canal_v, espessura_max):
    """
    Binariza SO o que parece linha vertical escura.

    O blackhat com kernel (espessura_max, 1) devolve, para cada pixel, o
    quanto ele e mais escuro que a vizinhanca horizontal. Uma linha fina
    responde forte; uma mancha escura mais larga que o kernel some.
    """
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (espessura_max, 1))
    blackhat = cv2.morphologyEx(canal_v, cv2.MORPH_BLACKHAT, kernel)
    limiar = max(
        settings.RECORTE_BLACKHAT_PISO,
        float(np.percentile(blackhat, settings.RECORTE_BLACKHAT_PERCENTIL))
        * settings.RECORTE_BLACKHAT_FATOR,
    )
    return (blackhat > limiar).astype(np.uint8)


# ---------------------------------------------------------------------------
# 2. Correcao de inclinacao
# ---------------------------------------------------------------------------


def _cisalhar(mascara, tangente):
    """Endireita as linhas verticais girando em torno da LINHA DO MEIO."""
    if abs(tangente) < 1e-6:
        return mascara
    altura, largura = mascara.shape
    matriz = np.float32([[1, tangente, -tangente * altura / 2.0], [0, 1, 0]])
    return cv2.warpAffine(mascara, matriz, (largura, altura),
                          flags=cv2.INTER_NEAREST,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def _estimar_inclinacao(mascara, graus_max, fator=0.4, topo=8):
    """
    Angulo que deixa a projecao por coluna mais concentrada.

    Busca em duas passadas (grossa e fina) sobre uma copia menor - o angulo
    nao precisa de resolucao de pixel, so de meio grau.
    """
    pequena = cv2.resize(mascara, None, fx=fator, fy=fator,
                         interpolation=cv2.INTER_NEAREST)

    def nitidez(graus):
        projecao = _cisalhar(pequena, float(np.tan(np.radians(graus)))).sum(0)
        # Soma dos maiores valores: premia POUCAS colunas muito cheias.
        return float(np.sort(projecao)[-topo:].sum())

    grosso = max(np.linspace(-graus_max, graus_max, 13), key=nitidez)
    # A busca fina fica presa na faixa configurada: sem o clamp, um passo
    # grosso na ponta (-3) abriria a fina ate -3.5.
    fino = max(np.clip(np.linspace(grosso - 0.5, grosso + 0.5, 9),
                       -graus_max, graus_max), key=nitidez)
    return float(np.tan(np.radians(fino))), float(fino)


# ---------------------------------------------------------------------------
# 3. Perfil por coluna e picos
# ---------------------------------------------------------------------------


def _perfil_colunas(mascara_reta, altura_min_frac, ponte_frac):
    """
    Fracao da altura coberta por cada coluna.

    O fechamento costura falhas da linha (uma praga em cima dela, um trecho
    apagado); a abertura exige que a coluna seja contigua por pelo menos
    `altura_min_frac` da foto - e o que separa linha da grade de sujeira.
    """
    altura = mascara_reta.shape[0]
    imagem = mascara_reta * 255
    ponte = cv2.getStructuringElement(
        cv2.MORPH_RECT, (1, _impar(altura * ponte_frac, 3)))
    imagem = cv2.morphologyEx(imagem, cv2.MORPH_CLOSE, ponte)
    corrida = cv2.getStructuringElement(
        cv2.MORPH_RECT, (1, _impar(altura * altura_min_frac, 9)))
    imagem = cv2.morphologyEx(imagem, cv2.MORPH_OPEN, corrida)
    return (imagem > 0).sum(0).astype(np.float32) / altura


def _picos(perfil, distancia_min, limiar):
    """Grupos de colunas acima do limiar -> (centro, forca, inicio, fim)."""
    acima = np.where(perfil > limiar)[0]
    if len(acima) == 0:
        return []
    grupos = [[acima[0]]]
    for coluna in acima[1:]:
        if coluna - grupos[-1][-1] <= distancia_min:
            grupos[-1].append(coluna)
        else:
            grupos.append([coluna])
    saida = []
    for grupo in grupos:
        grupo = np.array(grupo)
        peso = perfil[grupo]
        centro = float((grupo * peso).sum() / peso.sum())
        saida.append((centro, float(peso.max()), int(grupo[0]), int(grupo[-1])))
    return saida


# ---------------------------------------------------------------------------
# 4. Escolha do par de linhas
# ---------------------------------------------------------------------------


def _fortes(picos, forca_relativa):
    """
    Picos que podem servir de BORDA do quadrante.

    Dois cortes, e os dois importam:
      * relativo - descarta o que e muito mais fraco que a melhor linha da
        propria foto (sujeira, texto impresso na armadilha);
      * absoluto - uma linha de grade de verdade atravessa a foto. Sem este
        piso, uma foto fora de foco (sem grade nenhuma) fecha um "par" com
        dois borroes e o recorte sai de qualquer lugar.
    """
    if not picos:
        return []
    piso = max(settings.RECORTE_FORCA_MINIMA,
               forca_relativa * max(p[1] for p in picos))
    return sorted((p for p in picos if p[1] >= piso), key=lambda p: p[0])


def _par_central(picos, centro, minimo_px, maximo_px, forca_relativa):
    """
    Par de linhas CONSECUTIVAS com largura plausivel e meio mais central.

    Consecutivas e o ponto-chave: escolher "a linha mais forte de um lado" e
    "a mais forte do outro" e o que fazia o recorte pular uma linha e
    entregar dois quadrantes colados.
    """
    fortes = _fortes(picos, forca_relativa)
    candidatos = [(a, b) for a, b in zip(fortes, fortes[1:])
                  if minimo_px <= b[0] - a[0] <= maximo_px]
    if not candidatos:
        return None
    return min(candidatos,
               key=lambda ab: abs((ab[0][0] + ab[1][0]) / 2.0 - centro))


def _passo_da_grade(picos, minimo_px, maximo_px):
    """
    Largura tipica da celula neste eixo, medida entre linhas consecutivas.

    Aqui NAO se filtra por forca: a faixa de largura ja rejeita vao que nao
    seja de celula, e uma linha meio apagada continua marcando a posicao
    certa. Filtrar por forca aqui foi o que fez o resgate por passo desistir
    de fotos que tinham a informacao na mao.
    """
    ordenados = sorted(picos, key=lambda p: p[0])
    vaos = [b[0] - a[0] for a, b in zip(ordenados, ordenados[1:])
            if minimo_px <= b[0] - a[0] <= maximo_px]
    return float(np.median(vaos)) if vaos else None


def _par_por_passo(picos, centro, passo, minimo_px, maximo_px):
    """
    Reconstroi o par quando uma das linhas esta ocluida.

    Ancora na linha visivel e projeta a rede da grade ate a celula que
    contem o centro da foto. A celula pode cair parcialmente fora da foto -
    e o caso legitimo de a camera ter enquadrado so um pedaco dela.

    A ancora aqui e mais exigente que uma borda comum (RECORTE_FORCA_ANCORA
    em vez de RECORTE_FORCA_MINIMA): o par inteiro vai ser deduzido dela, e
    um borrao numa foto fora de foco nao pode virar a origem de um recorte.
    """
    if not passo or not (minimo_px <= passo <= maximo_px):
        return None
    fortes = [p for p in sorted(picos, key=lambda p: p[0])
              if p[1] >= settings.RECORTE_FORCA_ANCORA]
    if not fortes:
        return None
    melhor = None
    for ancora in fortes:
        salto = np.floor((centro - ancora[0]) / passo)
        inicio = ancora[0] + salto * passo
        fim = inicio + passo
        distancia = abs((inicio + fim) / 2.0 - centro)
        if melhor is None or distancia < melhor[0]:
            # Linha sintetica: sem extensao real, inicio/fim colapsam no
            # centro, e a confianca do par e a da ancora que a gerou.
            forca = ancora[1]
            melhor = (distancia,
                      (inicio, forca, inicio, inicio),
                      (fim, forca, fim, fim))
    return (melhor[1], melhor[2]) if melhor else None


# ---------------------------------------------------------------------------
# 5. Deteccao de um eixo
# ---------------------------------------------------------------------------


def _detectar_eixo(canal, minimo_px, maximo_px, espessura_max, distancia_min,
                   graus_max, niveis, forca_relativa, passo_externo=None):
    """
    Acha as duas linhas que delimitam a celula central ao longo do eixo X de
    `canal`.

    O eixo Y da foto e resolvido pela MESMA funcao, passando o canal
    transposto: linha horizontal transposta e linha vertical. Um unico
    caminho de codigo atende os dois eixos.

    `passo_externo` e o passo ja medido no outro eixo. A celula e proxima de
    quadrada, entao ele serve de estimativa aqui - e e o que salva o eixo em
    que so uma das linhas aparece no enquadramento.
    """
    extensao = canal.shape[1]
    centro = extensao / 2.0
    mascara = _mascara_linhas(canal, espessura_max)
    tangente, graus = _estimar_inclinacao(mascara, graus_max)
    reta = _cisalhar(mascara, tangente)
    # O mesmo cisalhamento no BRILHO: e nele que a espessura da linha e
    # medida depois (ver _extensao_da_linha). Retificado, porque a media de
    # uma linha torta borra a coluna e engorda a medida.
    brilho = _cisalhar(canal, tangente).mean(0)

    picos, nivel, par, passo, origem = [], None, None, None, None
    for altura_min_frac, limiar in niveis:
        nivel = (altura_min_frac, limiar)
        perfil = _perfil_colunas(reta, altura_min_frac,
                                 settings.RECORTE_PONTE_FRAC)
        picos = _picos(perfil, distancia_min, limiar)
        par = _par_central(picos, centro, minimo_px, maximo_px,
                           forca_relativa)
        if par:
            origem, passo = "par_direto", par[1][0] - par[0][0]
            break

    if par is None:
        # Linha ocluida: recupera o passo e projeta a celula central.
        # O passo do OUTRO eixo vem na frente: ele sai de um par de linhas
        # que casou de verdade, enquanto o vao medido aqui pode ser entre
        # duas linhas que sobreviveram por acaso ao nivel mais frouxo.
        passo, origem = None, None
        candidatos = ((passo_externo, "passo_do_outro_eixo"),
                      (_passo_da_grade(picos, minimo_px, maximo_px),
                       "passo_proprio"))
        for tentativa, nome in candidatos:
            par = _par_por_passo(picos, centro, tentativa, minimo_px, maximo_px)
            if par:
                passo, origem = tentativa, nome
                break

    return {
        "par": par,
        "picos": picos,
        "brilho": brilho,
        "espessura_max": espessura_max,
        "passo": passo,
        "tangente": tangente,
        "graus": graus,
        "nivel": nivel,
        "origem": origem,
    }


def _nucleo_da_linha(brilho, pico, limiar, sentido):
    """
    Trecho escuro do grupo que faz FRONTEIRA com o quadrante.

    O agrupamento junta o que estiver a menos de RECORTE_DISTANCIA_MIN_FRAC
    de distancia, entao um grupo pode conter duas coisas escuras separadas
    por papel - tipicamente a moldura preta da foto e a linha da grade logo
    depois dela. Quem manda no corte e a que encosta no quadrante; a outra
    esta atras e nao interessa.

    Ancorar no centro do pico nao serve para isso: num grupo desses o centro
    cai no vao claro (ou dentro da moldura, dependendo da escala em que a
    deteccao roda) e o corte fica instavel.

    `sentido` +1 quando o quadrante esta do lado dos indices maiores (linha
    de inicio do eixo) e -1 no outro caso.
    """
    a = int(max(0, min(pico[2], len(brilho) - 1)))
    b = int(max(a, min(pico[3], len(brilho) - 1)))
    escuros = np.flatnonzero(brilho[a:b + 1] <= limiar)
    if len(escuros) == 0:
        return None
    trechos = np.split(escuros, np.flatnonzero(np.diff(escuros) > 1) + 1)
    trecho = trechos[-1] if sentido > 0 else trechos[0]
    return a + int(round((trecho[0] + trecho[-1]) / 2.0))


def _extensao_da_linha(eixo, pico, sentido):
    """
    (beirada de fora, beirada de dentro) de UMA linha, medidas no BRILHO.

    A mascara do blackhat marca o nucleo escuro da linha; a tinta desbota
    nas beiradas e nao responde ao realce. Cortar pela mascara entrega meia
    linha - foi o que o acervo mostrou (o corte caia no meio do traco nos
    dois acervos). Aqui a linha vai ate onde o brilho ja voltou quase todo
    o caminho ate o papel.

    O papel e medido em volta DESTA linha, nunca na foto inteira: a
    iluminacao cai para as pontas do quadro (nas amarelas, 183 de um lado
    contra 167 do outro). Com um papel unico para toda a foto, a linha do
    lado escuro nunca "recupera" e a medida dispara ate o teto.

    Devolve (inicio, fim, e_beirada_do_papel).
    """
    brilho = eixo["brilho"]
    n = len(brilho)
    centro = min(max(int(round(pico[0])), 0), n - 1)
    vizinhanca = int(eixo["espessura_max"])
    janela = brilho[max(0, centro - vizinhanca):centro + vizinhanca + 1]
    # p90 e o papel: a janela e quase toda papel, com a linha no meio.
    papel = float(np.percentile(janela, 90))
    # O fundo e o ponto mais escuro do GRUPO da mascara, nao da vizinhanca
    # do centro: quando o agrupamento junta duas coisas escuras separadas
    # por papel (a moldura da foto e a linha logo abaixo dela), o centro do
    # pico cai no vao claro entre as duas e o contraste medido ali seria de
    # papel contra papel - a medida perderia o sentido.
    grupo = brilho[int(max(0, min(pico[2], centro - 2))):
                   int(max(pico[3], centro + 2)) + 1]
    fundo = float(min(grupo.min(), brilho[max(0, centro - 2):centro + 3].min()))
    if papel - fundo < 1.0:                    # sem contraste: sem medida
        return float(pico[2]), float(pico[3]), False
    limiar = papel - settings.RECORTE_RECUPERACAO_FRAC * (papel - fundo)

    # Reancora no trecho escuro que encosta no quadrante (ver _nucleo).
    nucleo = _nucleo_da_linha(brilho, pico, limiar, sentido)
    if nucleo is not None:
        centro = nucleo
    limite = eixo["espessura_max"] / 2.0
    inicio = centro
    while (inicio > 0 and centro - inicio < limite
           and brilho[inicio - 1] <= limiar):
        inicio -= 1
    fim = centro
    while (fim < n - 1 and fim - centro < limite
           and brilho[fim + 1] <= limiar):
        fim += 1

    # Sub-pixel: a beirada quase nunca cai em cima de uma amostra. Cada
    # amostra do perfil vale 1/RECORTE_FATOR_DETECCAO pixels na foto cheia
    # (8, na calibracao de producao), entao parar no indice inteiro joga
    # fora meia dezena de pixels de traco. O cruzamento com o limiar sai
    # por interpolacao linear entre a ultima amostra escura e a primeira
    # clara.
    def cruzamento(dentro_idx, fora_idx):
        if not 0 <= fora_idx < n:
            return float(dentro_idx)
        escura, clara = float(brilho[dentro_idx]), float(brilho[fora_idx])
        if clara <= limiar or clara <= escura:
            return float(dentro_idx)
        return dentro_idx + (clara - limiar) / (clara - escura) * (
            fora_idx - dentro_idx)

    beirada_inicio = cruzamento(inicio, inicio - 1)
    beirada_fim = cruzamento(fim, fim + 1)

    # Escuro que vai ate a beirada da FOTO sem clarear nao e linha da grade:
    # e o fim do papel, com o fundo do microscopio atras. Acontece na
    # amarela, onde a celula sai do enquadramento e a borda da placa entra
    # no lugar da linha horizontal. Ali o corte tem que parar onde o PAPEL
    # acaba, e nao onde o escuro acaba - senao a entrega leva o fundo preto
    # junto. Colapsar a linha na beirada de dentro faz exatamente isso.
    if inicio <= 0:
        return beirada_fim, beirada_fim, True
    if fim >= n - 1:
        return beirada_inicio, beirada_inicio, True

    # A mascara nunca pode ficar de fora: ela e o nucleo, e ja e linha.
    return (min(beirada_inicio, float(pico[2])),
            max(beirada_fim, float(pico[3])), False)


def _linhas_medidas(eixo, extensao):
    """
    Cada linha do par com a SUA beirada de dentro e a SUA espessura.

    Por lado, e nao uma espessura so para os dois: as duas linhas raramente
    saem iguais na foto (uma ponta da imagem pega mais luz que a outra), e
    obrigar as duas a usarem a mesma medida deixa um lado com margem de
    papel e o outro com o traco cortado ao meio.

    Sobram duas travas contra medida absurda - uma linha inflada por algo
    encostado nela (moldura, sombra) nao pode arrastar o corte junto:

      * grupo MUITO mais largo que o da parceira nao esta medindo so linha:
        tem coisa escura colada. A largura da parceira vale para as duas -
        a beirada de DENTRO continua sendo a propria, que a sujeira de fora
        nao desloca. Lado que e beirada do PAPEL fica de fora dessa troca:
        ali a espessura zero e o resultado certo, nao uma medida ruim;
      * e nenhuma passa do teto do realce (RECORTE_ESPESSURA_MAX_FRAC), a
        maior coisa que ainda conta como linha.

    Devolve {"inicio": (dentro, espessura), "fim": (dentro, espessura)}.
    """
    inicio_linha, fim_linha = eixo["par"]
    ini_fora, ini_dentro, ini_papel = _extensao_da_linha(eixo, inicio_linha, +1)
    fim_dentro, fim_fora, fim_papel = _extensao_da_linha(eixo, fim_linha, -1)
    larguras = [ini_dentro - ini_fora, fim_fora - fim_dentro]

    grupos = [linha[3] - linha[2] + 1 for linha in (inicio_linha, fim_linha)]
    limite = settings.RECORTE_LINHA_INFLADA
    if grupos[0] > limite * grupos[1] and not ini_papel:
        larguras[0] = larguras[1]
    elif grupos[1] > limite * grupos[0] and not fim_papel:
        larguras[1] = larguras[0]

    teto = extensao * settings.RECORTE_ESPESSURA_MAX_FRAC
    larguras = [float(min(max(largura, 0.0), teto)) for largura in larguras]
    return {"inicio": (ini_dentro, larguras[0]),
            "fim": (fim_dentro, larguras[1])}


def _bordas(eixo, extensao_perpendicular, margem_frac, extensao, borda):
    """
    Converte o par de linhas em (inicio, fim) do recorte.

    Tudo e contado a partir da beirada de DENTRO de cada linha - a unica que
    e sempre confiavel. A beirada de fora e o centro do pico se deslocam
    quando alguma coisa escura esta encostada na linha pelo lado de fora
    (moldura, sombra); do lado de dentro so existe o proprio quadrante.

      dentro      beirada de dentro + folga  -> quadrante sem traco
      linha       - espessura da linha       -> o traco, e nada depois dele
      meia_linha  - metade da espessura      -> meio traco de cada lado

    A beirada de dentro e a espessura saem do BRILHO, nao da mascara do
    blackhat (ver _extensao_da_linha): a mascara marca so o nucleo escuro
    do traco, e cortar por ela entregava meia linha.

    Nos dois modos que entregam traco o corte para NA linha: a beirada
    externa dela vira a beirada do arquivo, sem margem nenhuma de papel do
    lado de fora. E o que a montagem da placa pede - quadrado encostando em
    quadrado.

    O preco esta na grade torta. O corte e alinhado aos eixos e a linha
    deriva ate |tan| * metade da dimensao perpendicular nas pontas, entao
    conter a linha inteira exigiria justamente a margem que nao pode
    existir: numa ponta ela sai um pouco do quadro (no acervo azul, 18 px
    de 4800 na mediana; 71 px na foto mais torta de todas). Sobra margem ou
    sobra traco - nao da para ter os dois num recorte retangular.

    A folga configurada (`margem_frac` mais a deriva) so vale para
    'dentro', onde ela empurra o corte para longe da linha.
    """
    medidas = _linhas_medidas(eixo, extensao)
    (dentro_inicio, espessura_inicio) = medidas["inicio"]
    (dentro_fim, espessura_fim) = medidas["fim"]
    if borda == settings.RECORTE_BORDA_DENTRO:
        folga = (extensao * margem_frac
                 + abs(eixo["tangente"]) * extensao_perpendicular / 2.0)
        inicio, fim = dentro_inicio + folga, dentro_fim - folga
    else:
        fatia = (1.0 if borda == settings.RECORTE_BORDA_LINHA else 0.5)
        inicio = dentro_inicio - espessura_inicio * fatia
        fim = dentro_fim + espessura_fim * fatia
    return int(round(inicio)), int(round(fim))


def _profundidade_preta(escuro, caixa, eixo, ponta):
    """
    Quantas fileiras de PRETO existem a partir da beirada desta ponta.

    Em pixels da mascara (meia escala). Conta so fileira quase inteira
    escura (RECORTE_MOLDURA_FRACAO_MIN): uma praga ou uma folha na borda
    escurece um pedaco da fileira, nao ela toda.
    """
    y1, y2, x1, x2 = (max(0, c) // 2 for c in caixa)
    if y2 - y1 < 1 or x2 - x1 < 1:
        return 0
    janela = escuro[y1:y2, x1:x2]
    if janela.size == 0:
        return 0
    fileiras = janela.mean(1) if eixo == "y" else janela.mean(0)
    if ponta == "fim":
        fileiras = fileiras[::-1]
    densas = fileiras >= settings.RECORTE_MOLDURA_FRACAO_MIN
    return int(len(densas) if densas.all() else np.argmin(densas))


def _e_moldura(escuro, caixa, eixo, ponta, espessura):
    """
    O preto encostado nesta ponta e MOLDURA, e nao o traco da grade?

    Com o corte parando na propria linha, a fileira da beirada e escura por
    definicao - perguntar "e escura?" nao separa mais nada. O que separa e
    a PROFUNDIDADE: o traco tem a espessura medida e acaba, com papel logo
    depois; a moldura (borda da placa, fundo do microscopio) segue escura
    para dentro. Quando o detector ancora na beirada escura da foto - o que
    acontece nas celulas que saem pelo enquadramento - e isso que aparece.

    O limite e a espessura MEDIDA do traco daquela ponta, com 10% de folga.
    E uma medida generosa por natureza - ela ja inclui o ombro claro da
    tinta, enquanto o preto conta so o nucleo - entao preto mais fundo que
    ela nao e traco. Na amarela e assim que a borda preta da placa, que
    entra no lugar da linha horizontal quando a celula sai pelo
    enquadramento, e barrada.
    """
    # A mascara esta em meia escala; a espessura, em px de deteccao.
    limite = espessura / 2.0 * 1.1 + 1
    return _profundidade_preta(escuro, caixa, eixo, ponta) > limite


def _ponta_final(sem_traco, aparada, com_traco):
    """
    Decide UMA ponta do recorte depois que a moldura ja foi aparada.

    O aparo roda sobre a caixa SEM traco (ver `detectar_crop_box`). Se ele
    nao mexeu nesta ponta, a linha da grade entra no quadrante como pedido.
    Se mexeu, havia faixa preta ali e quem manda e a ponta aparada: devolver
    a linha significaria devolver a moldura junto com ela.
    """
    return com_traco if aparada == sem_traco else aparada


# ---------------------------------------------------------------------------
# 6. Moldura (o que esta FORA da armadilha)
# ---------------------------------------------------------------------------


def _aparar_pontas(fracao_escura, fracao_min, corte_max):
    """
    (inicio, fim) apos jogar fora as pontas de moldura.

    Corta ate a ponta MAIS FUNDA de moldura dentro da janela permitida, e
    nao ate a primeira linha boa: a moldura costuma deixar uma fresta de
    papel para fora da faixa preta, e parar nessa fresta era o que ainda
    deixava um tarjao preto na entrega.
    """
    n = len(fracao_escura)
    teto = int(n * corte_max)
    moldura = [i for i in range(teto) if fracao_escura[i] >= fracao_min]
    inicio = max(moldura) + 1 if moldura else 0
    moldura = [i for i in range(max(inicio, n - teto), n)
               if fracao_escura[i] >= fracao_min]
    fim = min(moldura) if moldura else n
    return inicio, fim


def _mapa_escuro(imagem):
    """
    Mascara do que e quase PRETO, em meia escala.

    Meia escala porque moldura e sombra sao faixas largas: meio pixel de
    precisao sobra, e custa um quarto do trabalho. O criterio e brilho, NAO
    "nao parece papel de armadilha": uma folha caida sobre a borda tambem
    nao parece papel, mas e conteudo do quadrante.
    """
    valor = cv2.cvtColor(
        cv2.resize(imagem, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA),
        cv2.COLOR_BGR2HSV)[..., 2]
    return valor < settings.RECORTE_MOLDURA_BRILHO_MAX


def _cortar_moldura(escuro, y1, y2, x1, x2):
    """
    Apara, DENTRO do quadrante ja escolhido, as faixas PRETAS das pontas.

    O alvo e especifico: a lateral da placa, o fundo do microscopio, a
    sombra da moldura - tudo quase preto.

    Duas travas seguram o resto: a linha precisa ser quase toda escura
    (RECORTE_MOLDURA_FRACAO_MIN) e o corte nunca passa de
    RECORTE_MOLDURA_CORTE_MAX_FRAC de cada ponta.
    """
    # A analise so enxerga o quadrante: o que acontece no quadrante vizinho
    # nao pode encolher o nosso.
    jy1, jx1 = y1 // 2, x1 // 2
    janela = escuro[jy1:max(jy1 + 1, y2 // 2), jx1:max(jx1 + 1, x2 // 2)]
    fracao_min = settings.RECORTE_MOLDURA_FRACAO_MIN
    corte_max = settings.RECORTE_MOLDURA_CORTE_MAX_FRAC

    # O aparo volta como RECUO de cada ponta, nao como coordenada absoluta:
    # a janela foi medida na metade da escala e o arredondamento da divisao
    # por 2 comeria um pixel de cada lado mesmo sem nada a aparar.
    altura_janela, largura_janela = janela.shape
    inicio_y, fim_y = _aparar_pontas(janela.mean(1), fracao_min, corte_max)
    inicio_x, fim_x = _aparar_pontas(janela.mean(0), fracao_min, corte_max)
    return (y1 + inicio_y * 2, y2 - (altura_janela - fim_y) * 2,
            x1 + inicio_x * 2, x2 - (largura_janela - fim_x) * 2)


# ---------------------------------------------------------------------------
# Deteccao
# ---------------------------------------------------------------------------


def detectar_crop_box(imagem_para_deteccao, perfil=None, margem_frac=None,
                      cortar_moldura=None, forca_relativa=None, eixos=None,
                      borda=None):
    """
    Descobre o quadrante central. Retorna coordenadas em pixels E em fracoes.

    perfil          'auto' (default), 'amarela' ou 'azul'. So aperta a faixa
                    de largura aceita para o quadrante - o algoritmo e o
                    mesmo nos tres.
    eixos           'ambos' (default) recorta tambem no eixo vertical, o que
                    tira a linha horizontal da grade e as tiras dos
                    quadrantes de cima e de baixo. 'so_vertical' recorta so
                    as laterais e entrega a altura inteira da foto.
    borda           de que lado da linha da grade o corte cai: 'linha'
                    (default) entrega o traco inteiro nos quatro lados,
                    'dentro' entrega o quadrante sem traco e 'meia_linha'
                    parte a linha ao meio com o quadrante vizinho.
    margem_frac     folga, em fracao da largura, aplicada a partir da BORDA
                    da linha - para dentro em 'dentro', para fora em 'linha'.
    cortar_moldura  apara as faixas de borda que nao sao papel da armadilha.
    forca_relativa  quao fraco um pico pode ser, em relacao ao mais forte,
                    para ainda contar como linha da grade.
    """
    perfil_cor = resolver_perfil(perfil)
    borda = resolver_borda(borda)
    margem_frac = (settings.RECORTE_MARGEM_FRAC if margem_frac is None
                   else margem_frac)
    cortar_moldura = (settings.RECORTE_CORTAR_MOLDURA if cortar_moldura is None
                      else cortar_moldura)
    forca_relativa = (settings.RECORTE_FORCA_RELATIVA if forca_relativa is None
                      else forca_relativa)
    eixos = str(settings.RECORTE_EIXOS if eixos is None else eixos).lower()

    altura, largura = imagem_para_deteccao.shape[:2]
    canal = cv2.medianBlur(
        cv2.cvtColor(imagem_para_deteccao, cv2.COLOR_BGR2HSV)[..., 2], 5)

    # A celula e proxima de quadrada, entao os limites de tamanho valem nos
    # dois eixos - sempre em pixels, sempre derivados da largura da foto.
    minimo_px = perfil_cor["largura_min_frac"] * largura
    maximo_px = perfil_cor["largura_max_frac"] * largura
    comuns = dict(
        espessura_max=_impar(largura * settings.RECORTE_ESPESSURA_MAX_FRAC, 15),
        distancia_min=max(10, int(largura * settings.RECORTE_DISTANCIA_MIN_FRAC)),
        graus_max=settings.RECORTE_INCLINACAO_MAX_GRAUS,
        niveis=settings.RECORTE_NIVEIS,
        forca_relativa=forca_relativa,
    )

    eixo_x = _detectar_eixo(canal, minimo_px, maximo_px, **comuns)

    # Transposta: a linha horizontal vira vertical e cai no mesmo detector.
    # O eixo Y roda tambem no modo 'so_vertical' quando X falhou, porque a
    # celula e proxima de quadrada e a linha horizontal costuma sobreviver
    # justamente nas fotos em que a vertical ficou coberta (folha, sujeira).
    eixo_y = None
    if eixos == "ambos" or eixo_x["par"] is None:
        eixo_y = _detectar_eixo(np.ascontiguousarray(canal.T),
                                minimo_px, maximo_px,
                                passo_externo=eixo_x["passo"], **comuns)
        if eixo_x["par"] is None and eixo_y["passo"]:
            # Segunda chance para X, agora com o passo vindo de Y.
            eixo_x = _detectar_eixo(canal, minimo_px, maximo_px,
                                    passo_externo=eixo_y["passo"], **comuns)

    # Duas caixas saem do mesmo par de linhas: a SEM TRACO (o corte pela
    # beirada de dentro) e a que sera entregue, que em 'linha'/'meia_linha'
    # abraca a linha da grade. Sao iguais quando borda == 'dentro'.
    sucesso = eixo_x["par"] is not None
    dentro = settings.RECORTE_BORDA_DENTRO
    # Espessura por PONTA, para a trava de moldura saber o que esperar de
    # traco em cada uma das quatro bordas.
    vazio = {"inicio": (0.0, 0.0), "fim": (0.0, 0.0)}
    medidas = {
        "x": _linhas_medidas(eixo_x, largura) if sucesso else vazio,
        "y": (_linhas_medidas(eixo_y, largura)
              if eixo_y and eixo_y["par"] else vazio),
    }
    if sucesso:
        x1_sem, x2_sem = _bordas(eixo_x, altura, margem_frac, largura, dentro)
        x1, x2 = _bordas(eixo_x, altura, margem_frac, largura, borda)
    else:
        x1_sem, x2_sem = x1, x2 = 0, largura
    y1_sem, y2_sem = y1, y2 = 0, altura
    if eixos == "ambos" and eixo_y and eixo_y["par"] is not None:
        y1_sem, y2_sem = _bordas(eixo_y, largura, margem_frac, largura, dentro)
        y1, y2 = _bordas(eixo_y, largura, margem_frac, largura, borda)

    if cortar_moldura:
        escuro = _mapa_escuro(imagem_para_deteccao)
        # O aparo mede a caixa SEM TRACO de proposito: com a linha encostada
        # na borda, ela mesma seria lida como faixa preta de moldura e sairia
        # cortada - justamente o que a entrega precisa manter.
        ay1, ay2, ax1, ax2 = _cortar_moldura(escuro, y1_sem, y2_sem,
                                             x1_sem, x2_sem)
        y1, y2 = _ponta_final(y1_sem, ay1, y1), _ponta_final(y2_sem, ay2, y2)
        x1, x2 = _ponta_final(x1_sem, ax1, x1), _ponta_final(x2_sem, ax2, x2)

        # ...e depois de encostar o corte no traco, cada ponta responde por
        # si: se o preto daquela beirada for FUNDO demais para ser traco, o
        # que entrou foi moldura, e a ponta volta para o corte sem traco -
        # limpo por construcao. Nao se aplica a 'dentro', que nunca avanca.
        if borda != settings.RECORTE_BORDA_DENTRO:
            def moldura(eixo_nome, ponta):
                return _e_moldura(escuro, (y1, y2, x1, x2), eixo_nome, ponta,
                                  medidas[eixo_nome][ponta][1])

            if moldura("y", "inicio"):
                y1 = max(y1, y1_sem)
            if moldura("y", "fim"):
                y2 = min(y2, y2_sem)
            if moldura("x", "inicio"):
                x1 = max(x1, x1_sem)
            if moldura("x", "fim"):
                x2 = min(x2, x2_sem)

    x1, x2 = max(0, min(x1, largura)), max(0, min(x2, largura))
    y1, y2 = max(0, min(y1, altura)), max(0, min(y2, altura))
    if x2 - x1 < 50 or y2 - y1 < 50:
        sucesso = False
        y1, y2, x1, x2 = 0, altura, 0, largura

    return {
        "sucesso": sucesso,
        "perfil": perfil_cor["nome"],
        "eixos": eixos,
        "borda": borda,
        "origem": eixo_x["origem"] if sucesso else None,
        "origem_y": eixo_y["origem"] if eixo_y else None,
        "nivel": eixo_x["nivel"],
        "inclinacao_graus": round(eixo_x["graus"], 3),
        "passo": (round(eixo_x["passo"], 1) if eixo_x["passo"] else None),
        "picos_v": [int(round(p[0])) for p in eixo_x["picos"]],
        "forcas_v": [round(p[1], 3) for p in eixo_x["picos"]],
        "picos_h": ([int(round(p[0])) for p in eixo_y["picos"]] if eixo_y else []),
        "forca_par": (round(min(eixo_x["par"][0][1], eixo_x["par"][1][1]), 3)
                      if eixo_x["par"] else None),
        "crop_box_px": (y1, y2, x1, x2),
        "crop_box_frac": (y1 / altura, y2 / altura, x1 / largura, x2 / largura),
        "dim_deteccao": (altura, largura),
    }


def recortar_em_resolucao_cheia(caminho_imagem, fator_deteccao=None, perfil=None,
                                margem_frac=None, cortar_moldura=None,
                                forca_relativa=None, borda=None):
    """
    Pipeline em 2 etapas:
    1. Detecta crop box em versao reduzida (rapido)
    2. Aplica crop na imagem original em resolucao cheia (preserva qualidade)
    """
    fator_deteccao = (settings.RECORTE_FATOR_DETECCAO if fator_deteccao is None
                      else fator_deteccao)

    imagem_cheia = cv2.imread(str(caminho_imagem))
    if imagem_cheia is None:
        # Resgate para caminhos com acentos no Windows.
        imagem_cheia = imread_fallback(caminho_imagem)
    if imagem_cheia is None:
        return None, None

    h_cheia, w_cheia = imagem_cheia.shape[:2]

    # O fator e calibrado para foto de microscopio (9600 px de largura). Numa
    # foto menor ele deixaria a linha da grade com menos de um pixel e a
    # deteccao falharia sem motivo, entao ele afrouxa ate a largura minima de
    # trabalho. Nunca AMPLIA a imagem: o teto e 1.0.
    fator_deteccao = min(1.0, max(
        fator_deteccao, settings.RECORTE_LARGURA_MINIMA_DETECCAO / max(1, w_cheia)))

    img_reduzida = cv2.resize(imagem_cheia, None,
                              fx=fator_deteccao, fy=fator_deteccao,
                              interpolation=cv2.INTER_AREA)

    info = detectar_crop_box(
        img_reduzida,
        perfil=perfil,
        margem_frac=margem_frac,
        cortar_moldura=cortar_moldura,
        forca_relativa=forca_relativa,
        borda=borda,
    )

    frac_y1, frac_y2, frac_x1, frac_x2 = info["crop_box_frac"]
    y1_cheia = int(frac_y1 * h_cheia)
    y2_cheia = int(frac_y2 * h_cheia)
    x1_cheia = int(frac_x1 * w_cheia)
    x2_cheia = int(frac_x2 * w_cheia)

    info["dim_original"] = (h_cheia, w_cheia)
    inteira = (y1_cheia, y2_cheia, x1_cheia, x2_cheia) == (0, h_cheia, 0, w_cheia)

    if not info["sucesso"] and inteira:
        # Deteccao falhou e nao houve nem aparo de moldura: devolve a foto
        # inteira, como no comportamento validado no Colab.
        del img_reduzida
        gc.collect()
        return imagem_cheia, info

    recortada_cheia = imagem_cheia[y1_cheia:y2_cheia, x1_cheia:x2_cheia].copy()

    del imagem_cheia, img_reduzida
    gc.collect()

    info["crop_box_cheia_px"] = (y1_cheia, y2_cheia, x1_cheia, x2_cheia)
    info["dim_recortada"] = recortada_cheia.shape[:2]
    return recortada_cheia, info


# ---------------------------------------------------------------------------
# Worker de paralelismo (camada nova - nao altera a logica acima)
# ---------------------------------------------------------------------------
# Precisa ser uma funcao de modulo (top-level) para ser picklavel pelo
# ProcessPoolExecutor no Windows (start method = spawn).


def processar_foto(
    caminho_entrada: str | Path,
    pasta_saida: str | Path | None = None,
    formato: str | None = None,
    lote_id: str | None = None,
    fator_deteccao: float | None = None,
    perfil: str | None = None,
    borda: str | None = None,
    margem_frac: float | None = None,
    cortar_moldura: bool | None = None,
    forca_relativa: float | None = None,
    nome_saida: str | None = None,
    pular_existentes: bool | None = None,
    mover_falhas: bool = True,
) -> dict:
    """
    Recorta UMA foto e grava o quadrante em pasta_saida.

    perfil           cor da armadilha ('auto', 'amarela', 'azul'). 'auto'
                     atende as duas; os nomeados so apertam a faixa de
                     largura aceita, para lotes em que o 'auto' hesita.
    borda            de que lado da linha da grade o corte cai: 'linha'
                     (default) entrega o quadrante COM os quatro tracos,
                     'dentro' sem nenhum, 'meia_linha' com metade de cada.
    nome_saida       nome do arquivo de saida SEM extensao. E por aqui que a
                     renomeacao acontece quando ela nao materializa a pasta
                     02_renomeadas: o quadrante ja nasce como VARD1.png
                     (ou a1.png), sem copiar o lote inteiro antes.
    pular_existentes se o quadrante de destino ja existe, nao reprocessa -
                     retomar um lote interrompido custa so o que faltava.
    mover_falhas     se a foto problematica deve ser MOVIDA para _falhas. O
                     pipeline so liga isso quando esta lendo de uma pasta
                     intermediaria (02_renomeadas): arquivo original de
                     usuario nao e movido de lugar, apenas registrado.

    Roda dentro de um worker: nunca levanta excecao para fora - qualquer
    problema volta no campo 'erro' do dicionario de resultado, para que uma
    foto ruim nao derrube o lote inteiro.
    """
    inicio = time.perf_counter()
    caminho_entrada = Path(caminho_entrada)
    pasta_saida = Path(pasta_saida or settings.PASTA_RECORTADAS)
    formato = formato or settings.RECORTE_FORMATO_SAIDA
    pular_existentes = (settings.RECORTE_PULAR_EXISTENTES
                        if pular_existentes is None else pular_existentes)
    caminho_saida = pasta_saida / (
        f"{nome_saida or caminho_entrada.stem}{extensao_do_formato(formato)}"
    )

    resultado = {
        "arquivo": caminho_entrada.name,
        "caminho_entrada": str(caminho_entrada),
        "saida": None,
        "sucesso": False,
        "deteccao_ok": False,
        "pulado": False,
        "erro": None,
        "info": None,
        "duracao_seg": 0.0,
    }

    try:
        if pular_existentes and caminho_saida.exists():
            resultado["saida"] = str(caminho_saida)
            resultado["sucesso"] = True
            resultado["pulado"] = True
            logger.debug("%s: quadrante ja existia, pulado.", caminho_saida.name)
            return resultado

        pasta_saida.mkdir(parents=True, exist_ok=True)

        recortada, info = recortar_em_resolucao_cheia(
            str(caminho_entrada),
            fator_deteccao=fator_deteccao,
            perfil=perfil,
            borda=borda,
            margem_frac=margem_frac,
            cortar_moldura=cortar_moldura,
            forca_relativa=forca_relativa,
        )

        if recortada is None:
            resultado["erro"] = "Imagem ilegivel (cv2.imread retornou None)"
            registrar_falha(caminho_entrada, "recorte", resultado["erro"],
                            lote_id=lote_id, mover_arquivo=mover_falhas)
            return resultado

        resultado["info"] = info
        resultado["deteccao_ok"] = bool(info.get("sucesso"))

        if not info.get("sucesso"):
            motivo = (
                "Deteccao das linhas da grade falhou "
                f"(picos verticais encontrados: {len(info.get('picos_v', []))})"
            )
            if settings.RECORTE_FALHA_DETECCAO_E_ERRO:
                # Modo estrito: nao gera o quadrante e manda a foto para
                # _falhas, para conferencia manual.
                resultado["erro"] = motivo
                registrar_falha(caminho_entrada, "recorte", motivo, info,
                                lote_id=lote_id, mover_arquivo=mover_falhas)
                return resultado
            # Comportamento validado no Colab: devolve a imagem CHEIA.
            logger.warning(
                "%s: %s - salvando a imagem CHEIA sem recorte.",
                caminho_entrada.name, motivo,
            )
            if settings.RECORTE_REGISTRAR_JSON_SEM_DETECCAO:
                registrar_falha(caminho_entrada, "recorte_sem_deteccao", motivo,
                                info, lote_id=lote_id, mover_arquivo=False)

        salvar_recortada(recortada, str(caminho_saida), formato=formato)

        resultado["saida"] = str(caminho_saida)
        resultado["sucesso"] = True
        logger.debug(
            "%s -> %s | original %s -> recortada %s | origem=%s inclinacao=%s",
            caminho_entrada.name, caminho_saida.name,
            info.get("dim_original"), info.get("dim_recortada"),
            info.get("origem"), info.get("inclinacao_graus"),
        )
    except Exception as exc:  # nenhuma foto pode derrubar o lote
        resultado["erro"] = f"{type(exc).__name__}: {exc}"
        logger.exception("Erro ao recortar %s", caminho_entrada.name)
        registrar_falha(caminho_entrada, "recorte", resultado["erro"],
                        lote_id=lote_id, mover_arquivo=mover_falhas)
    finally:
        resultado["duracao_seg"] = time.perf_counter() - inicio
        if not resultado["pulado"]:
            gc.collect()

    return resultado


def processar_item(item, **kwargs) -> dict:
    """
    Adaptador para a fila de paralelismo.

    Aceita tanto um caminho solto quanto a tupla (caminho, nome_de_saida)
    produzida pelo plano de nomeacao. Precisa ser uma funcao de modulo para
    ser picklavel pelo ProcessPoolExecutor no Windows.
    """
    if isinstance(item, (tuple, list)):
        caminho, nome_saida = (list(item) + [None])[:2]
    else:
        caminho, nome_saida = item, None
    return processar_foto(caminho, nome_saida=nome_saida, **kwargs)
