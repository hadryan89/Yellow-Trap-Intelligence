"""
Protocolo 1 - Nomeacao das imagens.

As fotos chegam da camera/microscopio com nomes arbitrarios (DSC0001.JPG,
IMG_042.jpg, ...) e recebem um nome novo antes do recorte. Ha dois esquemas:

  grid       a1, a2, ..., a10, b1, ..., d10  - posicao na placa YellowTrap,
             limitado a QUANTIDADE_ESPERADA fotos por lote;
  sequencial VARD0, VARD1, VARD2, ...        - sem limite de quantidade.

Nos dois casos a ordem vem da ORDEM NATURAL dos arquivos (img2 antes de
img10), que por sua vez vem do operador: as fotos sao tiradas em sequencia.

MATERIALIZACAO
--------------
Renomear nao exige, necessariamente, escrever um segundo lote em disco. O
recorte SEMPRE grava um arquivo novo, entao o nome novo pode ser aplicado
direto nele. Por isso o plano de nomeacao e separado da sua materializacao:

  virtual  - nada e escrito em 02_renomeadas (default dos lotes grandes);
  hardlink - link em 02_renomeadas: instantaneo e sem espaco extra;
  copiar   - copia byte-a-byte com conferencia MD5 (comportamento do Colab);
  mover    - move o arquivo (esvazia a entrada).

A copia com verificacao le o arquivo de origem UMA vez (o MD5 e calculado
durante a copia) e o destino outra - a garantia byte-a-byte e a mesma do
Colab com 1/3 menos de I/O.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from config import settings
from src.utils import listar_imagens, natural_key, obter_logger, registrar_falha

logger = obter_logger(__name__)

__all__ = [
    "natural_key",
    "ItemNomeacao",
    "PlanoNomeacao",
    "ResultadoNomeacao",
    "mapear_grid",
    "mapear_sequencial",
    "planejar_nomeacao",
    "aplicar_plano",
    "proximo_indice_sequencial",
    "gerar_mapeamento",
    "calcular_hash_md5",
    "renomear_com_verificacao",
    "criar_zip_sem_compressao",
    "verificar_integridade_zip",
    "executar_renomeacao",
]


# ---------------------------------------------------------------------------
# Estruturas do plano
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ItemNomeacao:
    """Uma foto e o nome que ela recebe."""

    origem: Path
    nome_novo: str  # com extensao: 'a1.jpg' | 'VARD0.jpg'
    indice: int

    @property
    def stem_novo(self) -> str:
        """Nome sem extensao - e o que o recorte usa para gravar a saida."""
        return Path(self.nome_novo).stem


@dataclass(slots=True)
class PlanoNomeacao:
    """O de-para completo, antes de qualquer escrita em disco."""

    itens: list[ItemNomeacao] = field(default_factory=list)
    total_arquivos: int = 0
    total_alvos: int | None = None  # None = sem teto (modo sequencial)
    ignorados: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.itens)

    @property
    def mapeamento(self) -> list[tuple[str, str]]:
        """Compatibilidade: [(nome_original, nome_novo), ...]."""
        return [(item.origem.name, item.nome_novo) for item in self.itens]


@dataclass(slots=True)
class ResultadoNomeacao:
    """
    Saida da etapa 1.

    `itens` e o que a etapa de recorte consome: pares
    (caminho_a_recortar, nome_de_saida_sem_extensao).
    """

    itens: list[tuple[str, str]] = field(default_factory=list)
    estrategia: str = settings.ESTRATEGIA_VIRTUAL
    hashes: dict[str, str] = field(default_factory=dict)
    falhas: list[dict] = field(default_factory=list)
    zip: str | None = None
    zip_ok: bool | None = None


# ---------------------------------------------------------------------------
# Esquemas de nomeacao
# ---------------------------------------------------------------------------


def mapear_grid(arquivos, letras=None, numeros=None) -> tuple[list[ItemNomeacao], int]:
    """
    Nomes do grid da placa na ordem a1..a10, b1..d10.

    `arquivos`: lista de caminhos JA ordenados naturalmente.
    Retorna (itens, total_de_posicoes_do_grid). Fotos que passarem do numero
    de posicoes ficam de fora - o grid tem tamanho fixo.
    """
    letras = letras if letras is not None else settings.LETRAS_COLUNAS
    numeros = numeros if numeros is not None else settings.NUMEROS_LINHAS
    nomes_alvo = [f"{letra}{numero}" for letra in letras for numero in numeros]

    itens = []
    for i in range(min(len(arquivos), len(nomes_alvo))):
        origem = Path(arquivos[i])
        itens.append(ItemNomeacao(origem=origem,
                                  nome_novo=f"{nomes_alvo[i]}{origem.suffix.lower()}",
                                  indice=i))
    return itens, len(nomes_alvo)


def mapear_sequencial(arquivos, prefixo: str | None = None,
                      digitos: int | None = None,
                      inicio: int | None = None) -> list[ItemNomeacao]:
    """
    Nomes sequenciais VARD0, VARD1, VARD2, ... sem teto de quantidade.

    `arquivos`: lista de caminhos JA ordenados naturalmente.
    `digitos` e a largura MINIMA do contador (1 = sem zeros a esquerda); o
    numero nunca e truncado, so ganha zeros ate atingir essa largura.
    """
    prefixo = settings.SEQUENCIAL_PREFIXO if prefixo is None else prefixo
    digitos = settings.SEQUENCIAL_DIGITOS if digitos is None else digitos
    inicio = settings.SEQUENCIAL_INICIO if inicio is None else inicio

    itens = []
    for deslocamento, caminho in enumerate(arquivos):
        origem = Path(caminho)
        numero = inicio + deslocamento
        itens.append(ItemNomeacao(
            origem=origem,
            nome_novo=f"{prefixo}{numero:0{digitos}d}{origem.suffix.lower()}",
            indice=deslocamento,
        ))
    return itens


def proximo_indice_sequencial(pastas, prefixo: str | None = None,
                              digitos: int | None = None,
                              inicio: int | None = None) -> int:
    """
    Maior indice ja usado nas pastas + 1 (ou `inicio`, se nao houver nenhum).

    Permite que varios envios entrem no mesmo acervo sem colisao de nomes.
    """
    prefixo = settings.SEQUENCIAL_PREFIXO if prefixo is None else prefixo
    digitos = settings.SEQUENCIAL_DIGITOS if digitos is None else digitos
    inicio = settings.SEQUENCIAL_INICIO if inicio is None else inicio

    if isinstance(pastas, (str, Path)):
        pastas = [pastas]

    maior = None
    for pasta in pastas:
        pasta = Path(pasta)
        if not pasta.exists():
            continue
        with os.scandir(pasta) as entradas:
            for entrada in entradas:
                if not entrada.is_file():
                    continue
                stem = Path(entrada.name).stem
                if not stem.startswith(prefixo):
                    continue
                sufixo = stem[len(prefixo):]
                if sufixo.isdigit():
                    valor = int(sufixo)
                    if maior is None or valor > maior:
                        maior = valor
    return inicio if maior is None else maior + 1


def planejar_nomeacao(arquivos, modo: str | None = None, prefixo: str | None = None,
                      digitos: int | None = None, inicio: int | None = None,
                      letras=None, numeros=None) -> PlanoNomeacao:
    """
    Monta o de-para completo SEM tocar no disco.

    E a peca que o sistema chamador pode inspecionar (ou exibir ao usuario)
    antes de autorizar o processamento.
    """
    modo = modo or settings.MODO_PADRAO
    arquivos = [Path(a) for a in arquivos]
    total = len(arquivos)

    if modo == settings.MODO_GRID:
        itens, total_alvos = mapear_grid(arquivos, letras, numeros)
        ignorados = [a.name for a in arquivos[len(itens):]]
        return PlanoNomeacao(itens=itens, total_arquivos=total,
                             total_alvos=total_alvos, ignorados=ignorados)

    if modo == settings.MODO_SEQUENCIAL:
        itens = mapear_sequencial(arquivos, prefixo, digitos, inicio)
        return PlanoNomeacao(itens=itens, total_arquivos=total, total_alvos=None)

    # MODO_RECORTE: preserva o nome de origem.
    itens = [ItemNomeacao(origem=a, nome_novo=a.name, indice=i)
             for i, a in enumerate(arquivos)]
    return PlanoNomeacao(itens=itens, total_arquivos=total, total_alvos=None)


# ---------------------------------------------------------------------------
# Materializacao do plano
# ---------------------------------------------------------------------------


def calcular_hash_md5(caminho_arquivo, chunk: int | None = None) -> str:
    """Calcula MD5 do arquivo lendo em blocos (funciona com arquivos grandes)."""
    chunk = chunk or settings.RENOMEACAO_CHUNK_MD5
    hash_md5 = hashlib.md5()
    with open(caminho_arquivo, 'rb') as f:
        for bloco in iter(lambda: f.read(chunk), b''):
            hash_md5.update(bloco)
    return hash_md5.hexdigest()


def _copiar_calculando_hash(origem: Path, destino: Path,
                            chunk: int | None = None) -> str:
    """
    Copia origem -> destino e devolve o MD5 do que foi LIDO da origem.

    Uma unica leitura da origem serve para copiar e para hashear, em vez das
    duas do fluxo original (hash + copia). Os bytes gravados sao os mesmos.
    """
    chunk = chunk or settings.RENOMEACAO_CHUNK_MD5
    hash_md5 = hashlib.md5()
    with open(origem, 'rb') as entrada, open(destino, 'wb') as saida:
        for bloco in iter(lambda: entrada.read(chunk), b''):
            hash_md5.update(bloco)
            saida.write(bloco)
    shutil.copystat(origem, destino, follow_symlinks=True)
    return hash_md5.hexdigest()


def _materializar_item(item: ItemNomeacao, pasta_destino: Path, estrategia: str,
                       verificar_md5: bool) -> tuple[ItemNomeacao, Path | None,
                                                     str | None, str | None]:
    """
    Aplica UMA estrategia a UM item.

    Retorna (item, caminho_destino, hash_md5, erro). Nunca levanta excecao:
    um arquivo problematico nao pode derrubar a etapa inteira.
    """
    destino = pasta_destino / item.nome_novo
    try:
        if estrategia == settings.ESTRATEGIA_HARDLINK:
            if destino.exists():
                destino.unlink()
            try:
                os.link(item.origem, destino)
            except OSError:
                # Volume diferente ou sistema de arquivos sem hardlink.
                shutil.copy2(item.origem, destino)
            return item, destino, None, None

        if estrategia == settings.ESTRATEGIA_MOVER:
            if destino.exists():
                destino.unlink()
            shutil.move(str(item.origem), str(destino))
            return item, destino, None, None

        # ESTRATEGIA_COPIAR
        if verificar_md5:
            hash_origem = _copiar_calculando_hash(item.origem, destino)
            hash_copia = calcular_hash_md5(destino)
            if hash_origem != hash_copia:
                return item, None, None, (
                    "MD5 da copia diferente do original (copia corrompida)"
                )
            return item, destino, hash_origem, None

        shutil.copy(item.origem, destino)
        return item, destino, None, None
    except Exception as exc:
        return item, None, None, f"{type(exc).__name__}: {exc}"


def aplicar_plano(plano: PlanoNomeacao, pasta_destino=None,
                  estrategia: str = settings.ESTRATEGIA_VIRTUAL,
                  verificar_md5: bool | None = None,
                  limpar_destino: bool | None = None,
                  workers: int | None = None,
                  lote_id: str | None = None,
                  registrar_falhas: bool = True) -> ResultadoNomeacao:
    """
    Materializa o plano e devolve os pares (caminho_a_recortar, stem_saida).

    Com estrategia 'virtual' nada e escrito: os pares apontam para os
    arquivos de origem e o nome novo e aplicado la na frente, pelo recorte.
    """
    verificar_md5 = (settings.RENOMEACAO_VERIFICAR_MD5
                     if verificar_md5 is None else verificar_md5)

    if estrategia == settings.ESTRATEGIA_VIRTUAL:
        return ResultadoNomeacao(
            itens=[(str(item.origem), item.stem_novo) for item in plano.itens],
            estrategia=estrategia,
        )

    pasta_destino = Path(pasta_destino or settings.PASTA_RENOMEADAS)
    pasta_destino.mkdir(parents=True, exist_ok=True)

    limpar = (settings.RENOMEACAO_LIMPAR_DESTINO
              if limpar_destino is None else limpar_destino)
    if limpar:
        removidos = 0
        with os.scandir(pasta_destino) as entradas:
            alvos = [entrada.path for entrada in entradas if entrada.is_file()]
        for caminho in alvos:
            try:
                os.remove(caminho)
                removidos += 1
            except OSError:
                pass
        if removidos:
            logger.debug("Destino limpo: %d arquivo(s) removido(s).", removidos)

    workers = max(1, int(workers or 1))
    resultado = ResultadoNomeacao(estrategia=estrategia)
    pares: list[tuple[str, str]] = []

    def _consumir(retorno) -> None:
        item, destino, hash_md5, erro = retorno
        if erro:
            resultado.falhas.append({
                "arquivo": item.origem.name,
                "etapa": "renomeacao",
                "motivo": erro,
            })
            logger.error("Falha ao materializar %s -> %s: %s",
                         item.origem.name, item.nome_novo, erro)
            if registrar_falhas:
                registrar_falha(item.origem, "renomeacao", erro,
                                {"nome_destino": item.nome_novo},
                                lote_id=lote_id, mover_arquivo=True)
            return
        if hash_md5:
            resultado.hashes[item.nome_novo] = hash_md5
        pares.append((str(destino), item.stem_novo))

    if workers == 1:
        for item in plano.itens:
            _consumir(_materializar_item(item, pasta_destino, estrategia,
                                         verificar_md5))
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for retorno in executor.map(
                lambda item: _materializar_item(item, pasta_destino, estrategia,
                                                verificar_md5),
                plano.itens,
            ):
                _consumir(retorno)

    # A ordem do plano e significativa (a1, a2, ...): as threads podem
    # devolver fora de ordem, entao reordenamos pelo indice original.
    posicao = {item.stem_novo: item.indice for item in plano.itens}
    pares.sort(key=lambda par: posicao.get(par[1], 0))
    resultado.itens = pares
    return resultado


# ---------------------------------------------------------------------------
# Funcoes validadas no Colab - contrato preservado
# ---------------------------------------------------------------------------


def gerar_mapeamento(pasta_origem, letras, numeros):
    """Gera lista [(nome_original, nome_novo), ...] baseado na ordem natural."""
    extensoes = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff'}
    arquivos = [arq.name for arq in Path(pasta_origem).iterdir()
                if arq.suffix.lower() in extensoes]
    arquivos = sorted(arquivos, key=natural_key)

    itens, total_alvos = mapear_grid(arquivos, letras, numeros)
    mapeamento = [(item.origem.name, item.nome_novo) for item in itens]
    return mapeamento, len(arquivos), total_alvos


def renomear_com_verificacao(pasta_origem, pasta_destino, mapeamento,
                             limpar_destino=True):
    """
    Copia byte-a-byte com verificacao MD5. Retorna (hashes, falhas).

    Mantida para compatibilidade com quem ja chamava assim; internamente usa
    o mesmo caminho de `aplicar_plano` (copia e conferencia identicas).
    """
    plano = PlanoNomeacao(
        itens=[ItemNomeacao(origem=Path(pasta_origem) / orig, nome_novo=novo,
                            indice=i)
               for i, (orig, novo) in enumerate(mapeamento)],
        total_arquivos=len(mapeamento),
    )
    resultado = aplicar_plano(
        plano, pasta_destino, estrategia=settings.ESTRATEGIA_COPIAR,
        verificar_md5=True, limpar_destino=limpar_destino, workers=1,
        registrar_falhas=False,
    )
    nomes_com_falha = {f["arquivo"] for f in resultado.falhas}
    falhas = [(orig, novo) for orig, novo in mapeamento if orig in nomes_com_falha]
    return resultado.hashes, falhas


def criar_zip_sem_compressao(pasta_origem, caminho_zip):
    """Cria ZIP em modo STORED (sem compressao, bytes preservados)."""
    if os.path.exists(caminho_zip):
        os.remove(caminho_zip)
    with zipfile.ZipFile(caminho_zip, 'w', zipfile.ZIP_STORED) as zf:
        for nome in sorted(os.listdir(pasta_origem)):
            caminho = os.path.join(pasta_origem, nome)
            zf.write(caminho, arcname=nome)


def verificar_integridade_zip(caminho_zip, hashes_esperados):
    """Confirma que os bytes dentro do zip batem com os hashes originais."""
    todos_ok = True
    resultados = {}
    with zipfile.ZipFile(caminho_zip, 'r') as zf:
        for novo in sorted(hashes_esperados.keys()):
            with zf.open(novo) as f:
                hash_md5 = hashlib.md5()
                for chunk in iter(lambda: f.read(1024 * 1024), b''):
                    hash_md5.update(chunk)
                hash_zip = hash_md5.hexdigest()
            ok = hash_zip == hashes_esperados[novo]
            resultados[novo] = ok
            if not ok:
                todos_ok = False
    return todos_ok, resultados


def gerar_zip_de_integridade(pasta_destino, hashes: dict[str, str],
                             lote_id: str | None = None,
                             verificar: bool | None = None) -> tuple[str | None,
                                                                     bool | None]:
    """Cria (e opcionalmente confere) o ZIP das fotos renomeadas."""
    if not hashes:
        return None, None
    verificar = (settings.RENOMEACAO_VERIFICAR_ZIP if verificar is None
                 else verificar)
    sufixo = f"_{lote_id}" if lote_id else ""
    settings.PASTA_ZIPS.mkdir(parents=True, exist_ok=True)
    caminho_zip = settings.PASTA_ZIPS / f"renomeadas{sufixo}.zip"
    criar_zip_sem_compressao(pasta_destino, str(caminho_zip))
    logger.info("ZIP (ZIP_STORED) criado: %s", caminho_zip)

    if not verificar:
        return str(caminho_zip), None

    zip_ok, resultados = verificar_integridade_zip(str(caminho_zip), hashes)
    if zip_ok:
        logger.info("Integridade do ZIP confirmada (%d arquivos).", len(resultados))
    else:
        divergentes = [n for n, ok in resultados.items() if not ok]
        logger.error("ZIP com %d arquivo(s) divergente(s): %s",
                     len(divergentes), divergentes)
    return str(caminho_zip), zip_ok


# ---------------------------------------------------------------------------
# Orquestracao da etapa (compatibilidade)
# ---------------------------------------------------------------------------


def executar_renomeacao(
    pasta_origem: Path | str | None = None,
    pasta_destino: Path | str | None = None,
    letras: list[str] | None = None,
    numeros: list[int] | None = None,
    lote_id: str | None = None,
    criar_zip: bool | None = None,
) -> dict:
    """
    Renomeacao no esquema do grid, materializando por copia com MD5.

    Mantida com a mesma assinatura e o mesmo relatorio de antes. O pipeline
    novo usa `planejar_nomeacao` + `aplicar_plano`; esta funcao continua util
    para quem quer apenas a etapa 1 no modo classico.
    """
    pasta_origem = Path(pasta_origem or settings.PASTA_ENTRADA)
    pasta_destino = Path(pasta_destino or settings.PASTA_RENOMEADAS)
    criar_zip = settings.RENOMEACAO_CRIAR_ZIP if criar_zip is None else criar_zip

    if not pasta_origem.exists():
        raise FileNotFoundError(f"Pasta de entrada nao existe: {pasta_origem}")

    arquivos = listar_imagens(pasta_origem)
    plano = planejar_nomeacao(arquivos, modo=settings.MODO_GRID,
                              letras=letras, numeros=numeros)
    logger.info(
        "Renomeacao: %d arquivo(s) encontrado(s), %d nome(s) alvo, %d mapeado(s).",
        plano.total_arquivos, plano.total_alvos, len(plano),
    )

    if plano.total_arquivos == 0:
        raise ValueError(f"Nenhuma imagem encontrada em {pasta_origem}")
    if plano.total_arquivos != plano.total_alvos:
        logger.warning(
            "Quantidade divergente: %d foto(s) para %d posicao(oes) do grid. "
            "Serao processadas %d.",
            plano.total_arquivos, plano.total_alvos, len(plano),
        )

    resultado = aplicar_plano(
        plano, pasta_destino, estrategia=settings.ESTRATEGIA_COPIAR,
        verificar_md5=True,
        limpar_destino=settings.RENOMEACAO_LIMPAR_DESTINO,
        workers=1, lote_id=lote_id,
    )
    logger.info("Renomeacao: %d arquivo(s) com MD5 conferido.",
                len(resultado.hashes))

    caminho_zip, zip_ok = (None, None)
    if criar_zip:
        caminho_zip, zip_ok = gerar_zip_de_integridade(
            pasta_destino, resultado.hashes, lote_id=lote_id)

    return {
        "mapeamento": plano.mapeamento,
        "hashes": resultado.hashes,
        "falhas": resultado.falhas,
        "total_arquivos": plano.total_arquivos,
        "total_alvos": plano.total_alvos,
        "zip": caminho_zip,
        "zip_ok": zip_ok,
    }
