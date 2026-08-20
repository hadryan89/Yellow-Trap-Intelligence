"""
Modo WATCHER - fica rodando vigiando data/01_entrada_bruta/.

Como funciona
-------------
1. O watchdog avisa quando chegam arquivos novos na pasta de entrada.
2. A cada ciclo (WATCHER_INTERVALO_POLL_SEG) o watcher agrupa os arquivos
   pendentes por prefixo (IMG_001..IMG_040 -> grupo "IMG_").
3. Um arquivo so entra no lote depois de ficar com o tamanho estavel por
   WATCHER_CICLOS_ESTABILIDADE ciclos - evita processar foto pela metade
   enquanto a camera/cartao ainda esta copiando.
4. O criterio de FECHAMENTO do lote depende do modo:

   * modo grid (--tamanho-lote N, default 40): o lote so fecha quando o
     grupo atinge N fotos estaveis. Lote INCOMPLETO nunca e processado - o
     watcher espera indefinidamente, avisando por WARNING a cada
     WATCHER_TIMEOUT_LOTE_INCOMPLETO_SEG segundos quantas fotos faltam;
   * modo sequencial (--tamanho-lote 0): nao existe "tamanho certo". O lote
     fecha por QUIETUDE - passados WATCHER_TIMEOUT_LOTE_INCOMPLETO_SEG
     segundos sem chegar arquivo novo, tudo o que estiver estavel e
     processado, sejam 12 ou 12.000 fotos.

5. Ao fechar, as fotos vao para 01_entrada_bruta/_lotes/<lote_id>/ e o
   pipeline (nomeacao + recorte) e disparado.
6. O estado vai para .watcher_state.json - reiniciar o watcher nao
   reprocessa nada.

Uso:
    python scripts/watcher.py
    python scripts/watcher.py --modo sequencial --tamanho-lote 0
    python scripts/watcher.py --uma-vez        # processa o que der e sai
    python scripts/watcher.py --tamanho-lote 20
    python scripts/watcher.py --resetar-estado
"""

from __future__ import annotations

import argparse
import json
import shutil
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from config import settings  # noqa: E402
from src.exportacao import FORMATOS_VALIDOS  # noqa: E402
from src.opcoes import OpcoesProcessamento  # noqa: E402
from src.pipeline import executar_processamento, novo_lote_id  # noqa: E402
from src.utils import (  # noqa: E402
    configurar_logging,
    garantir_pastas,
    listar_imagens,
    natural_key,
    obter_logger,
    prefixo_do_lote,
)

logger = obter_logger("watcher")

_PARAR = False


def _tratar_sinal(signum, frame):  # pragma: no cover - interativo
    global _PARAR
    if not _PARAR:
        logger.info("Sinal recebido - encerrando apos o ciclo atual...")
    _PARAR = True


# ---------------------------------------------------------------------------
# Estado persistido
# ---------------------------------------------------------------------------


def carregar_estado(caminho: Path) -> dict:
    """Le .watcher_state.json (ou devolve um estado vazio)."""
    if not caminho.exists():
        return {"versao": 1, "lotes": {}, "arquivos_processados": {}}
    try:
        estado = json.loads(caminho.read_text(encoding="utf-8"))
        estado.setdefault("lotes", {})
        estado.setdefault("arquivos_processados", {})
        return estado
    except Exception as exc:
        logger.error("Estado corrompido (%s): %s. Recomecando do zero.", caminho, exc)
        return {"versao": 1, "lotes": {}, "arquivos_processados": {}}


def salvar_estado(caminho: Path, estado: dict) -> None:
    """Grava o estado de forma atomica (tmp + replace)."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix(caminho.suffix + ".tmp")
    temporario.write_text(json.dumps(estado, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    temporario.replace(caminho)


# ---------------------------------------------------------------------------
# Deteccao de arquivos novos
# ---------------------------------------------------------------------------


class _RelogioDeEventos:
    """Guarda o instante do ultimo evento observado na pasta de entrada."""

    def __init__(self) -> None:
        self.ultimo_evento = time.monotonic()

    def marcar(self) -> None:
        self.ultimo_evento = time.monotonic()

    def inativo_ha(self) -> float:
        return time.monotonic() - self.ultimo_evento


def iniciar_observador(pasta: Path, relogio: _RelogioDeEventos):
    """
    Liga o watchdog na pasta de entrada.

    Devolve o Observer (ou None se o watchdog nao estiver instalado - nesse
    caso o watcher continua funcionando so com o polling do loop principal).
    """
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        logger.warning(
            "watchdog nao instalado - operando apenas por polling a cada %ds. "
            "Instale com: pip install watchdog", settings.WATCHER_INTERVALO_POLL_SEG,
        )
        return None

    class _Handler(FileSystemEventHandler):
        def on_created(self, event):
            if not event.is_directory:
                logger.debug("Evento: arquivo criado -> %s", event.src_path)
                relogio.marcar()

        def on_modified(self, event):
            if not event.is_directory:
                relogio.marcar()

        def on_moved(self, event):
            if not event.is_directory:
                logger.debug("Evento: arquivo movido -> %s", event.dest_path)
                relogio.marcar()

    observador = Observer()
    observador.schedule(_Handler(), str(pasta), recursive=False)
    observador.start()
    logger.info("watchdog ativo em %s", pasta)
    return observador


# ---------------------------------------------------------------------------
# Agrupamento e estabilidade
# ---------------------------------------------------------------------------


def atualizar_estabilidade(pendentes: list[Path], rastreador: dict) -> list[Path]:
    """
    Marca quais arquivos ja estao com o tamanho estavel.

    rastreador: {nome: {"tamanho": int, "ciclos": int}} - mutado no lugar.
    Retorna a lista de arquivos considerados prontos.
    """
    nomes_atuais = {p.name for p in pendentes}
    for nome in list(rastreador):
        if nome not in nomes_atuais:
            del rastreador[nome]

    prontos = []
    for caminho in pendentes:
        try:
            tamanho = caminho.stat().st_size
        except OSError:
            continue  # arquivo sumiu/esta travado; tenta no proximo ciclo

        registro = rastreador.setdefault(caminho.name, {"tamanho": -1, "ciclos": 0})
        if tamanho == registro["tamanho"] and tamanho > 0:
            registro["ciclos"] += 1
        else:
            registro["tamanho"] = tamanho
            registro["ciclos"] = 0

        if registro["ciclos"] >= settings.WATCHER_CICLOS_ESTABILIDADE:
            prontos.append(caminho)
    return prontos


def agrupar_por_prefixo(arquivos: list[Path]) -> dict[str, list[Path]]:
    """Agrupa por prefixo do nome (IMG_001.jpg / IMG_002.jpg -> 'IMG_')."""
    grupos: dict[str, list[Path]] = {}
    for caminho in arquivos:
        grupos.setdefault(prefixo_do_lote(caminho.name), []).append(caminho)
    for chave in grupos:
        grupos[chave].sort(key=lambda p: natural_key(p.name))
    return grupos


def arquivar_lote(arquivos: list[Path], lote_id: str) -> Path:
    """
    Move (ou copia) as fotos do lote para 01_entrada_bruta/_lotes/<lote_id>/.

    Isolar o lote garante que a renomeacao mapeie exatamente estas 40 fotos,
    sem misturar com sobras de lotes anteriores.
    """
    destino = settings.PASTA_LOTES_ARQUIVADOS / lote_id
    destino.mkdir(parents=True, exist_ok=True)
    for caminho in arquivos:
        alvo = destino / caminho.name
        if settings.WATCHER_ARQUIVAR_ENTRADA:
            shutil.move(str(caminho), str(alvo))
        else:
            shutil.copy2(str(caminho), str(alvo))
    logger.info("Lote %s isolado em %s (%d foto(s)).", lote_id, destino, len(arquivos))
    return destino


# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------


def processar_lote(arquivos: list[Path], estado: dict, caminho_estado: Path,
                   opcoes_base: OpcoesProcessamento) -> bool:
    """Isola o lote, roda o pipeline e atualiza o estado."""
    lote_id = novo_lote_id()
    nomes = [c.name for c in arquivos]
    logger.info("=" * 68)
    logger.info("LOTE FECHADO: %d foto(s) (%s ... %s)",
                len(arquivos), nomes[0], nomes[-1])

    pasta_lote = arquivar_lote(arquivos, lote_id)

    sumario = executar_processamento(
        opcoes_base.com(pasta_entrada=pasta_lote, lote_id=lote_id))

    processado_em = datetime.now().isoformat(timespec="seconds")
    estado["lotes"][lote_id] = {
        # Em lotes de milhares de fotos a lista completa inflaria o estado:
        # guardamos a contagem e as pontas, que e o que serve para auditoria.
        "total_arquivos": len(nomes),
        "primeiro": nomes[0],
        "ultimo": nomes[-1],
        "pasta_lote": str(pasta_lote),
        "processado_em": processado_em,
        "sucesso": sumario.sucesso,
        "recortadas_ok": sumario.recortadas_ok,
        "falhas": sumario.total_falhas,
        "pasta_saida": sumario.pasta_saida,
        "duracao_seg": round(sumario.duracao_seg, 2),
    }
    for nome in nomes:
        estado["arquivos_processados"][nome] = {"lote": lote_id, "em": processado_em}
    _podar_estado(estado)
    salvar_estado(caminho_estado, estado)
    logger.info("Estado atualizado: %d lote(s) processado(s) no total.",
                len(estado["lotes"]))
    return sumario.sucesso


def _podar_estado(estado: dict, maximo: int = 50_000) -> None:
    """
    Limita o historico de arquivos processados.

    As fotos ja foram movidas para _lotes/<lote_id>/, entao o historico e
    apenas uma rede de seguranca contra reprocessamento - nao pode crescer
    indefinidamente e transformar a leitura do estado num gargalo.
    """
    processados = estado.get("arquivos_processados", {})
    if len(processados) <= maximo:
        return
    ordenados = sorted(processados.items(), key=lambda par: par[1].get("em", ""))
    estado["arquivos_processados"] = dict(ordenados[-maximo:])
    logger.info("Historico de arquivos podado para os %d mais recentes.", maximo)


def loop(args) -> int:
    global _PARAR
    caminho_estado = Path(settings.WATCHER_ARQUIVO_ESTADO)
    estado = carregar_estado(caminho_estado)
    if args.forcar:
        logger.warning("--forcar: o historico de arquivos processados sera ignorado.")

    garantir_pastas(*settings.PASTAS_OBRIGATORIAS)

    opcoes_base = OpcoesProcessamento(
        modo=args.modo,
        workers=args.workers,
        formato=args.formato,
        perfil=args.perfil,
        estrategia_renomeacao=args.estrategia,
        continuar_numeracao=args.continuar,
        pular_existentes=args.pular_existentes,
    )

    relogio = _RelogioDeEventos()
    observador = iniciar_observador(settings.PASTA_ENTRADA, relogio)
    rastreador: dict[str, dict] = {}
    ultimo_aviso = 0.0
    tamanho_lote = max(0, args.tamanho_lote)
    # tamanho_lote == 0 -> o lote fecha por quietude, nao por contagem.
    por_quietude = tamanho_lote == 0
    ciclos = 0
    # Em --uma-vez ainda e preciso rodar os ciclos da checagem de
    # estabilidade, senao nenhum arquivo chega a ser considerado pronto.
    ciclos_maximos = settings.WATCHER_CICLOS_ESTABILIDADE + 1

    logger.info("=" * 68)
    logger.info("WATCHER ATIVO")
    logger.info("  Pasta vigiada ....... %s", settings.PASTA_ENTRADA)
    logger.info("  Modo ................ %s", args.modo)
    if por_quietude:
        logger.info("  Fechamento do lote .. por quietude (%ds sem arquivo novo)",
                    settings.WATCHER_TIMEOUT_LOTE_INCOMPLETO_SEG)
    else:
        logger.info("  Tamanho do lote ..... %d foto(s)", tamanho_lote)
    logger.info("  Poll ................ %ds", settings.WATCHER_INTERVALO_POLL_SEG)
    logger.info("  Lotes ja no historico %d", len(estado["lotes"]))
    logger.info("  Ctrl+C para encerrar.")
    logger.info("=" * 68)

    try:
        while not _PARAR:
            ciclos += 1
            processados = set() if args.forcar else set(estado["arquivos_processados"])
            pendentes = [
                caminho for caminho in listar_imagens(settings.PASTA_ENTRADA)
                if caminho.name not in processados
            ]
            prontos = atualizar_estabilidade(pendentes, rastreador)
            # Sem tamanho fixo o agrupamento por prefixo so atrapalharia: um
            # envio de milhares de fotos com nomes variados e UM lote.
            grupos = ({"todos": prontos} if prontos else {}) if por_quietude \
                else agrupar_por_prefixo(prontos)
            inativo = relogio.inativo_ha()
            quieto = inativo >= settings.WATCHER_TIMEOUT_LOTE_INCOMPLETO_SEG

            houve_trabalho = False
            for prefixo, arquivos in sorted(grupos.items()):
                if por_quietude:
                    # Sem tamanho fixo: fecha quando parou de chegar arquivo
                    # (ou imediatamente, no --uma-vez).
                    if not (quieto or args.uma_vez):
                        continue
                    lote = arquivos
                elif len(arquivos) >= tamanho_lote:
                    lote = arquivos[:tamanho_lote]
                else:
                    continue

                try:
                    processar_lote(lote, estado, caminho_estado, opcoes_base)
                except Exception as exc:
                    logger.exception("Erro ao processar o lote '%s': %s",
                                     prefixo, exc)
                houve_trabalho = True
                relogio.marcar()
                rastreador.clear()

            if not houve_trabalho and not por_quietude:
                if (pendentes and quieto
                        and time.monotonic() - ultimo_aviso
                        >= settings.WATCHER_TIMEOUT_LOTE_INCOMPLETO_SEG):
                    for prefixo, arquivos in sorted(agrupar_por_prefixo(pendentes).items()):
                        faltam = tamanho_lote - len(arquivos)
                        if faltam > 0:
                            logger.warning(
                                "Lote INCOMPLETO '%s': %d/%d foto(s) - faltam %d. "
                                "Aguardando (nada sera processado pela metade).",
                                prefixo, len(arquivos), tamanho_lote, faltam,
                            )
                    ultimo_aviso = time.monotonic()

            if args.uma_vez and (houve_trabalho or ciclos >= ciclos_maximos):
                if not houve_trabalho:
                    logger.info(
                        "--uma-vez: nenhum lote fechado apos %d ciclo(s) de "
                        "verificacao. Encerrando sem processar.", ciclos,
                    )
                break

            time.sleep(settings.WATCHER_INTERVALO_POLL_SEG)
    except KeyboardInterrupt:  # pragma: no cover - interativo
        logger.info("Interrompido pelo usuario.")
    finally:
        if observador is not None:
            observador.stop()
            observador.join(timeout=5)
        salvar_estado(caminho_estado, estado)
        logger.info("Watcher encerrado. Estado salvo em %s", caminho_estado)
    return 0


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="YellowTrap Pipeline - modo watcher (vigia a pasta de entrada)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--modo", choices=list(settings.MODOS_VALIDOS),
                        default=settings.MODO_PADRAO,
                        help="grid = a1..d10 | sequencial = VARD1 | "
                             "recorte = mantem o nome de origem")
    parser.add_argument("--tamanho-lote", type=int,
                        default=settings.QUANTIDADE_ESPERADA,
                        help="quantas fotos formam um lote; 0 = fecha o lote "
                             "por quietude (indicado com --modo sequencial)")
    parser.add_argument("--workers", type=int, default=None,
                        help="processos paralelos no recorte (default: CPUs - 1)")
    parser.add_argument("--formato", choices=list(FORMATOS_VALIDOS),
                        default=None,
                        help="formato dos quadrantes recortados")
    parser.add_argument("--perfil", choices=list(settings.RECORTE_PERFIS),
                        default=None,
                        help="cor da armadilha: auto atende amarela e azul "
                             "(padrao); amarela/azul travam a faixa esperada "
                             "do quadrante num lote dificil")
    parser.add_argument("--materializar", dest="estrategia",
                        choices=list(settings.ESTRATEGIAS_VALIDAS), default=None,
                        help="como gravar 02_renomeadas (virtual = nao grava)")
    parser.add_argument("--continuar", dest="continuar", action="store_true",
                        default=None,
                        help="modo sequencial: continua a numeracao de onde parou")
    parser.add_argument("--retomar", dest="pular_existentes", action="store_true",
                        default=None,
                        help="pula fotos cujo quadrante ja existe na saida")
    parser.add_argument("--uma-vez", action="store_true",
                        help="roda um unico ciclo e encerra (util em cron/n8n)")
    parser.add_argument("--forcar", action="store_true",
                        help="ignora o historico e reprocessa o que estiver na pasta")
    parser.add_argument("--resetar-estado", action="store_true",
                        help="apaga o .watcher_state.json e sai")
    parser.add_argument("--verbose", action="store_true", help="log DEBUG no console")
    return parser


def main() -> int:
    args = construir_parser().parse_args()
    configurar_logging(nivel_console="DEBUG" if args.verbose else None)

    if args.resetar_estado:
        caminho = Path(settings.WATCHER_ARQUIVO_ESTADO)
        if caminho.exists():
            caminho.unlink()
            logger.info("Estado removido: %s", caminho)
        else:
            logger.info("Nao havia estado para remover.")
        return 0

    signal.signal(signal.SIGINT, _tratar_sinal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _tratar_sinal)

    return loop(args)


if __name__ == "__main__":
    raise SystemExit(main())
