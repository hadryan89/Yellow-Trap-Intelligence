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
        "passo": passo,
        "tangente": tangente,
        "graus": graus,
        "nivel": nivel,
        "origem": origem,
    }


def _bordas(eixo, extensao_perpendicular, margem_frac, extensao):
    """
    Converte o par de linhas em (inicio, fim) do recorte.

    Corta a partir da BORDA DE DENTRO de cada linha - assim nem meia linha
    entra no quadrante - e ainda abre uma folga proporcional a inclinacao: a
    linha e reta no meio da foto, mas deriva ate |tan| * metade da dimensao
    perpendicular nas pontas.
    """
    inicio_linha, fim_linha = eixo["par"]
    margem = (extensao * margem_frac
              + abs(eixo["tangente"]) * extensao_perpendicular / 2.0)
    return (int(round(inicio_linha[3] + margem)),
            int(round(fim_linha[2] - margem)))


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


def _cortar_moldura(imagem, y1, y2, x1, x2):
    """
    Apara, DENTRO do quadrante ja escolhido, as faixas PRETAS das pontas.

    O alvo e especifico: a lateral da placa, o fundo do microscopio, a
    sombra da moldura - tudo quase preto. O criterio e brilho, NAO "nao
    parece papel de armadilha": uma folha caida sobre a borda tambem nao
    parece papel, mas e conteudo do quadrante e nao pode ser cortada.

    Duas travas seguram o resto: a linha precisa ser quase toda escura
    (RECORTE_MOLDURA_FRACAO_MIN) e o corte nunca passa de
    RECORTE_MOLDURA_CORTE_MAX_FRAC de cada ponta.
    """
    valor = cv2.cvtColor(
        cv2.resize(imagem, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA),
        cv2.COLOR_BGR2HSV)[..., 2]
    escuro = valor < settings.RECORTE_MOLDURA_BRILHO_MAX

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
                      cortar_moldura=None, forca_relativa=None, eixos=None):
    """
    Descobre o quadrante central. Retorna coordenadas em pixels E em fracoes.

    perfil          'auto' (default), 'amarela' ou 'azul'. So aperta a faixa
                    de largura aceita para o quadrante - o algoritmo e o
                    mesmo nos tres.
    eixos           'ambos' (default) recorta tambem no eixo vertical, o que
                    tira a linha horizontal da grade e as tiras dos
                    quadrantes de cima e de baixo. 'so_vertical' recorta so
                    as laterais e entrega a altura inteira da foto.
    margem_frac     folga interna, em fracao da largura, aplicada a partir da
                    BORDA de dentro de cada linha.
    cortar_moldura  apara as faixas de borda que nao sao papel da armadilha.
    forca_relativa  quao fraco um pico pode ser, em relacao ao mais forte,
                    para ainda contar como linha da grade.
    """
    perfil_cor = resolver_perfil(perfil)
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

    sucesso = eixo_x["par"] is not None
    x1, x2 = (_bordas(eixo_x, altura, margem_frac, largura) if sucesso
              else (0, largura))
    y1, y2 = 0, altura
    if eixos == "ambos" and eixo_y and eixo_y["par"] is not None:
        y1, y2 = _bordas(eixo_y, largura, margem_frac, largura)

    if cortar_moldura:
        y1, y2, x1, x2 = _cortar_moldura(imagem_para_deteccao, y1, y2, x1, x2)

    x1, x2 = max(0, min(x1, largura)), max(0, min(x2, largura))
    y1, y2 = max(0, min(y1, altura)), max(0, min(y2, altura))
    if x2 - x1 < 50 or y2 - y1 < 50:
        sucesso = False
        y1, y2, x1, x2 = 0, altura, 0, largura

    return {
        "sucesso": sucesso,
        "perfil": perfil_cor["nome"],
        "eixos": eixos,
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
                                forca_relativa=None):
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
