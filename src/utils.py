"""
Utilitarios compartilhados: logging estruturado, medicao de recursos,
registro de falhas e helpers de sistema de arquivos.
"""

from __future__ import annotations

import ctypes
import json
import logging
import logging.handlers
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from config import settings

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_LOGGING_CONFIGURADO = False


def configurar_logging(
    nivel_console: str | None = None,
    nivel_arquivo: str | None = None,
    arquivo_log: Path | None = None,
    forcar: bool = False,
) -> logging.Logger:
    """
    Configura o logging raiz do processo.

    - Handler de arquivo rotativo: 10 MB por arquivo, 5 backups.
    - Handler de console colorido (colorlog quando disponivel).
    - Formato: [YYYY-MM-DD HH:MM:SS] [LEVEL] [modulo] mensagem

    Idempotente: chamar varias vezes nao duplica handlers.
    """
    global _LOGGING_CONFIGURADO

    raiz = logging.getLogger()
    if _LOGGING_CONFIGURADO and not forcar:
        return raiz

    if forcar:
        for handler in list(raiz.handlers):
            raiz.removeHandler(handler)
            handler.close()

    nivel_console = nivel_console or settings.LOG_NIVEL_CONSOLE
    nivel_arquivo = nivel_arquivo or settings.LOG_NIVEL_ARQUIVO
    arquivo_log = Path(arquivo_log or settings.ARQUIVO_LOG)
    arquivo_log.parent.mkdir(parents=True, exist_ok=True)

    raiz.setLevel(logging.DEBUG)

    # --- arquivo (rotativo) ---
    handler_arquivo = logging.handlers.RotatingFileHandler(
        arquivo_log,
        maxBytes=settings.LOG_MAX_BYTES,
        backupCount=settings.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler_arquivo.setLevel(getattr(logging, nivel_arquivo.upper(), logging.DEBUG))
    handler_arquivo.setFormatter(
        logging.Formatter(settings.LOG_FORMATO, datefmt=settings.LOG_FORMATO_DATA)
    )
    raiz.addHandler(handler_arquivo)

    # --- console (colorido quando possivel) ---
    handler_console = logging.StreamHandler(sys.stdout)
    handler_console.setLevel(getattr(logging, nivel_console.upper(), logging.INFO))
    try:
        import colorlog

        handler_console.setFormatter(
            colorlog.ColoredFormatter(
                "%(log_color)s[%(asctime)s] [%(levelname)s] [%(name)s]%(reset)s %(message)s",
                datefmt=settings.LOG_FORMATO_DATA,
                log_colors={
                    "DEBUG": "cyan",
                    "INFO": "green",
                    "WARNING": "yellow",
                    "ERROR": "red",
                    "CRITICAL": "red,bg_white",
                },
            )
        )
    except ImportError:  # colorlog e opcional
        handler_console.setFormatter(
            logging.Formatter(settings.LOG_FORMATO, datefmt=settings.LOG_FORMATO_DATA)
        )
    raiz.addHandler(handler_console)

    _LOGGING_CONFIGURADO = True
    return raiz


def obter_logger(nome: str) -> logging.Logger:
    """Retorna um logger nomeado (usa o nome curto do modulo)."""
    if nome.startswith("src."):
        nome = nome[4:]
    return logging.getLogger(nome)


# ---------------------------------------------------------------------------
# Ordenacao natural (compartilhada entre renomeacao e watcher)
# ---------------------------------------------------------------------------


def natural_key(texto: str) -> list:
    """Ordena considerando numeros (img2 vem antes de img10)."""
    partes = re.split(r"(\d+)", texto)
    chave = []
    for p in partes:
        if p.isdigit():
            chave.append(int(p))
        else:
            chave.append(p.lower())
    return chave


def prefixo_do_lote(nome_arquivo: str) -> str:
    """
    Extrai o prefixo de agrupamento de um nome de arquivo.

    'IMG_001.jpg' -> 'IMG_'   |  'DSC0042.JPG' -> 'DSC'  |  'foto.png' -> 'foto'
    Usado pelo watcher para agrupar fotos de um mesmo lote.
    """
    stem = Path(nome_arquivo).stem
    return re.sub(r"\d+$", "", stem)


# ---------------------------------------------------------------------------
# Sistema de arquivos
# ---------------------------------------------------------------------------


def garantir_pastas(*pastas: Path) -> None:
    """Cria as pastas informadas (e pais) se nao existirem."""
    for pasta in pastas:
        Path(pasta).mkdir(parents=True, exist_ok=True)


def listar_imagens(pasta: Path, extensoes: Iterable[str] | None = None,
                   limite: int | None = None,
                   ordenar: bool = True) -> list[Path]:
    """
    Lista arquivos de imagem de uma pasta, em ordem natural.

    Usa os.scandir: em pastas com milhares de arquivos ele evita uma chamada
    de stat() por item (o tipo vem junto da leitura do diretorio), o que faz
    diferenca real no Windows.

    limite: devolve apenas os N primeiros (depois da ordenacao).
    """
    pasta = Path(pasta)
    if not pasta.exists():
        return []
    validas = {e.lower() for e in (extensoes or settings.EXTENSOES_IMAGEM)}

    arquivos: list[Path] = []
    with os.scandir(pasta) as entradas:
        for entrada in entradas:
            if os.path.splitext(entrada.name)[1].lower() not in validas:
                continue
            try:
                if not entrada.is_file():
                    continue
            except OSError:  # arquivo sumiu no meio da varredura
                continue
            arquivos.append(Path(entrada.path))

    if ordenar:
        arquivos.sort(key=lambda p: natural_key(p.name))
    if limite is not None and limite > 0:
        return arquivos[:limite]
    return arquivos


def contar_imagens(pasta: Path, extensoes: Iterable[str] | None = None) -> int:
    """Conta imagens sem materializar a lista de caminhos."""
    pasta = Path(pasta)
    if not pasta.exists():
        return 0
    validas = {e.lower() for e in (extensoes or settings.EXTENSOES_IMAGEM)}
    total = 0
    with os.scandir(pasta) as entradas:
        for entrada in entradas:
            if os.path.splitext(entrada.name)[1].lower() in validas:
                try:
                    if entrada.is_file():
                        total += 1
                except OSError:
                    continue
    return total


def limpar_pasta(pasta: Path, apenas_arquivos: bool = True) -> int:
    """
    Remove o conteudo de uma pasta. Retorna a quantidade removida.

    Um arquivo travado por outro processo nao interrompe a limpeza: o erro
    vira DEBUG e a varredura continua.
    """
    pasta = Path(pasta)
    if not pasta.exists():
        pasta.mkdir(parents=True, exist_ok=True)
        return 0

    logger = obter_logger(__name__)
    removidos = 0
    with os.scandir(pasta) as entradas:
        itens = [(entrada.path, entrada.is_dir()) for entrada in entradas]
    for caminho, e_pasta in itens:
        try:
            if e_pasta:
                if not apenas_arquivos:
                    shutil.rmtree(caminho)
                    removidos += 1
            else:
                os.remove(caminho)
                removidos += 1
        except OSError as exc:
            logger.debug("Nao foi possivel remover %s: %s", caminho, exc)
    return removidos


def tamanho_legivel(num_bytes: float) -> str:
    """Formata bytes em unidade legivel."""
    for unidade in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.1f} {unidade}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"


def formatar_duracao(segundos: float) -> str:
    """Formata uma duracao em h/min/s."""
    if segundos < 60:
        return f"{segundos:.2f}s"
    minutos, seg = divmod(segundos, 60)
    if minutos < 60:
        return f"{int(minutos)}min {seg:.1f}s"
    horas, minutos = divmod(minutos, 60)
    return f"{int(horas)}h {int(minutos)}min {seg:.0f}s"


# ---------------------------------------------------------------------------
# I/O de imagem resistente a caminhos com acentos (Windows)
# ---------------------------------------------------------------------------
# cv2.imread/cv2.imwrite usam a API ANSI do Windows e falham SILENCIOSAMENTE
# (retornam None/False) quando o caminho tem acentos - ex.: "C:\Users\...\
# Área de Trabalho\". As funcoes abaixo sao usadas apenas como RESGATE: o
# caminho normal continua sendo cv2.imread/cv2.imwrite, e o fallback so entra
# em acao se a chamada original falhar. Os bytes gerados sao identicos, pois
# imencode/imdecode usam exatamente o mesmo codec.


def imread_fallback(caminho: str | Path, flags: int | None = None):
    """Le uma imagem via np.fromfile + cv2.imdecode. Retorna None se falhar."""
    try:
        import cv2
        import numpy as np

        buffer = np.fromfile(str(caminho), dtype=np.uint8)
        if buffer.size == 0:
            return None
        return cv2.imdecode(buffer, cv2.IMREAD_COLOR if flags is None else flags)
    except Exception:
        return None


def imwrite_fallback(caminho: str | Path, imagem, params: list | None = None) -> bool:
    """Grava uma imagem via cv2.imencode + tofile. Retorna True se gravou."""
    try:
        import cv2

        caminho = Path(caminho)
        ok, buffer = cv2.imencode(caminho.suffix, imagem, params or [])
        if not ok:
            return False
        buffer.tofile(str(caminho))
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Medicao de memoria (sem dependencia externa obrigatoria)
# ---------------------------------------------------------------------------


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def medir_memoria_mb() -> float | None:
    """
    Retorna a memoria residente do processo atual em MB.

    Tenta psutil (se instalado), depois a API nativa do Windows, depois
    resource (Linux/macOS). Retorna None se nada estiver disponivel.
    """
    try:
        import psutil  # opcional

        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        pass

    if sys.platform == "win32":
        # GetProcessMemoryInfo vive em psapi.dll; nas versoes recentes do
        # Windows tambem existe como K32GetProcessMemoryInfo em kernel32.dll.
        for nome_dll, nome_funcao in (("psapi", "GetProcessMemoryInfo"),
                                      ("kernel32", "K32GetProcessMemoryInfo")):
            try:
                import ctypes.wintypes

                funcao = getattr(getattr(ctypes.windll, nome_dll), nome_funcao)
                funcao.argtypes = [
                    ctypes.wintypes.HANDLE,
                    ctypes.POINTER(_PROCESS_MEMORY_COUNTERS),
                    ctypes.c_ulong,
                ]
                funcao.restype = ctypes.wintypes.BOOL

                kernel32 = ctypes.windll.kernel32
                kernel32.GetCurrentProcess.restype = ctypes.wintypes.HANDLE

                contadores = _PROCESS_MEMORY_COUNTERS()
                contadores.cb = ctypes.sizeof(contadores)
                if funcao(kernel32.GetCurrentProcess(),
                          ctypes.byref(contadores), contadores.cb):
                    return contadores.WorkingSetSize / (1024 * 1024)
            except Exception:
                continue
        return None

    try:
        import resource

        uso = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reporta em KB; macOS em bytes.
        return uso / 1024 if sys.platform.startswith("linux") else uso / (1024 * 1024)
    except Exception:
        return None


class Cronometro:
    """Context manager que mede tempo e delta de memoria de um bloco."""

    def __init__(self, rotulo: str, logger: logging.Logger | None = None,
                 nivel: int = logging.INFO):
        self.rotulo = rotulo
        self.logger = logger or logging.getLogger("utils")
        self.nivel = nivel
        self.inicio = 0.0
        self.duracao = 0.0
        self.mem_inicio: float | None = None
        self.mem_fim: float | None = None

    def __enter__(self) -> "Cronometro":
        self.inicio = time.perf_counter()
        self.mem_inicio = medir_memoria_mb()
        self.logger.log(self.nivel, "%s - inicio", self.rotulo)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.duracao = time.perf_counter() - self.inicio
        self.mem_fim = medir_memoria_mb()
        if exc_type is None:
            self.logger.log(
                self.nivel,
                "%s - fim em %s | memoria: %s",
                self.rotulo,
                formatar_duracao(self.duracao),
                f"{self.mem_fim:.0f} MB" if self.mem_fim is not None else "n/d",
            )
        else:
            self.logger.error(
                "%s - ABORTADO apos %s: %s",
                self.rotulo,
                formatar_duracao(self.duracao),
                exc,
            )
        return False  # nunca engole a excecao


# ---------------------------------------------------------------------------
# Registro de falhas
# ---------------------------------------------------------------------------


def registrar_falha(
    caminho_arquivo: Path | str | None,
    etapa: str,
    motivo: str,
    detalhes: dict[str, Any] | None = None,
    lote_id: str | None = None,
    mover_arquivo: bool = True,
    pasta_falhas: Path | None = None,
) -> Path | None:
    """
    Registra uma falha em data/_falhas/.

    Grava um JSON com o motivo e (opcionalmente) move/copia o arquivo
    problematico para a mesma pasta. Retorna o caminho do JSON gerado.

    Nunca levanta excecao: registrar falha jamais pode derrubar o pipeline.
    """
    logger = obter_logger(__name__)
    try:
        pasta_falhas = Path(pasta_falhas or settings.PASTA_FALHAS)
        if lote_id:
            pasta_falhas = pasta_falhas / lote_id
        pasta_falhas.mkdir(parents=True, exist_ok=True)

        nome_base = Path(caminho_arquivo).name if caminho_arquivo else f"{etapa}_sem_arquivo"

        registro = {
            "arquivo": str(caminho_arquivo) if caminho_arquivo else None,
            "arquivo_nome": nome_base,
            "etapa": etapa,
            "motivo": motivo,
            "lote_id": lote_id,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "detalhes": _serializavel(detalhes or {}),
        }

        destino_arquivo = None
        if mover_arquivo and caminho_arquivo and Path(caminho_arquivo).exists():
            destino_arquivo = pasta_falhas / nome_base
            destino_arquivo = _caminho_unico(destino_arquivo)
            if settings.FALHAS_COPIAR_EM_VEZ_DE_MOVER:
                shutil.copy2(caminho_arquivo, destino_arquivo)
            else:
                shutil.move(str(caminho_arquivo), str(destino_arquivo))
            registro["arquivo_em_falhas"] = str(destino_arquivo)

        alvo_json = _caminho_unico(pasta_falhas / f"{Path(nome_base).stem}.json")
        alvo_json.write_text(
            json.dumps(registro, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.debug("Falha registrada em %s", alvo_json)
        return alvo_json
    except Exception as exc:  # pragma: no cover - defensivo
        logger.error("Nao foi possivel registrar a falha de %s: %s", caminho_arquivo, exc)
        return None


def _caminho_unico(caminho: Path) -> Path:
    """Evita sobrescrever registros antigos acrescentando _1, _2, ..."""
    if not caminho.exists():
        return caminho
    contador = 1
    while True:
        candidato = caminho.with_name(f"{caminho.stem}_{contador}{caminho.suffix}")
        if not candidato.exists():
            return candidato
        contador += 1


def _serializavel(valor: Any) -> Any:
    """Converte tipos numpy/Path/tuplas para algo que o json aceite."""
    if isinstance(valor, dict):
        return {str(k): _serializavel(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_serializavel(v) for v in valor]
    if isinstance(valor, Path):
        return str(valor)
    if hasattr(valor, "item") and callable(valor.item):  # numpy scalar
        try:
            return valor.item()
        except Exception:
            return str(valor)
    if isinstance(valor, (str, int, float, bool)) or valor is None:
        return valor
    return str(valor)


# ---------------------------------------------------------------------------
# Sumario de execucao
# ---------------------------------------------------------------------------


@dataclass
class SumarioLote:
    """
    Resultado consolidado de uma execucao do pipeline.

    Escala: contadores sao inteiros e o detalhamento das falhas e limitado a
    settings.SUMARIO_MAX_FALHAS_DETALHADAS entradas. Um lote de 2.000 fotos
    que der errado inteiro produz um relatorio legivel, nao um JSON de
    centenas de MB - e `falhas_total` continua exato.
    """

    lote_id: str
    modo: str = settings.MODO_PADRAO
    inicio: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    duracao_seg: float = 0.0
    total_entrada: int = 0
    renomeadas: int = 0
    ignoradas: int = 0            # fora do teto do grid / do limite pedido
    recortadas_ok: int = 0
    recortadas_sem_deteccao: int = 0
    recortadas_falha: int = 0
    puladas: int = 0              # ja existiam na saida (retomada)
    falhas: list[dict[str, Any]] = field(default_factory=list)
    falhas_total: int = 0
    falhas_por_etapa: dict[str, int] = field(default_factory=dict)
    pasta_saida: str | None = None
    memoria_pico_mb: float | None = None
    sucesso: bool = False
    # Snapshot das opcoes da execucao (inclui o `contexto` livre do chamador).
    # Deixa o relatorio auto-explicativo: da para reproduzir o lote so com ele.
    parametros: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Registro de falhas
    # ------------------------------------------------------------------

    def adicionar_falha(self, arquivo: Any, etapa: str, motivo: Any) -> None:
        """Contabiliza uma falha (o detalhe entra ate o teto configurado)."""
        self.falhas_total += 1
        self.falhas_por_etapa[etapa] = self.falhas_por_etapa.get(etapa, 0) + 1
        if len(self.falhas) < settings.SUMARIO_MAX_FALHAS_DETALHADAS:
            self.falhas.append({"arquivo": arquivo, "etapa": etapa,
                                "motivo": motivo})

    def estender_falhas(self, falhas: Iterable[dict[str, Any]]) -> None:
        for falha in falhas:
            self.adicionar_falha(falha.get("arquivo"), falha.get("etapa", "?"),
                                 falha.get("motivo"))

    @property
    def total_falhas(self) -> int:
        # max() protege quem ainda usa sumario.falhas.append() diretamente.
        return max(self.falhas_total, len(self.falhas))

    @property
    def sem_falhas(self) -> bool:
        """Lote perfeito: rodou ate o fim e nenhuma foto ficou pelo caminho."""
        return self.sucesso and self.total_falhas == 0

    @property
    def total_processadas(self) -> int:
        return self.recortadas_ok + self.recortadas_falha

    @property
    def fotos_por_segundo(self) -> float:
        return self.total_processadas / self.duracao_seg if self.duracao_seg else 0.0

    # ------------------------------------------------------------------
    # Serializacao
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        dados = _serializavel(asdict(self))
        dados["total_falhas"] = self.total_falhas
        dados["sem_falhas"] = self.sem_falhas
        dados["fotos_por_segundo"] = round(self.fotos_por_segundo, 3)
        dados["falhas_omitidas"] = max(0, self.total_falhas - len(self.falhas))
        return dados

    def salvar_json(self, caminho: Path) -> Path:
        caminho = Path(caminho)
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return caminho

    def linhas_relatorio(self) -> list[str]:
        """Texto do sumario final, uma linha por item."""
        linhas = [
            "=" * 68,
            f"SUMARIO DO LOTE {self.lote_id}  (modo: {self.modo})",
            "=" * 68,
            f"  Fotos de entrada .............. {self.total_entrada}",
            f"  Nomeadas ...................... {self.renomeadas}",
        ]
        if self.ignoradas:
            linhas.append(f"  Ignoradas (fora do lote) ...... {self.ignoradas}")
        if self.puladas:
            linhas.append(f"  Puladas (ja existiam) ......... {self.puladas}")
        linhas += [
            f"  Recortadas com sucesso ........ {self.recortadas_ok}",
            f"  Recortadas SEM deteccao ....... {self.recortadas_sem_deteccao}",
            f"  Falhas ........................ {self.total_falhas}",
        ]
        if self.pasta_saida:
            linhas.append(f"  Pasta de saida ................ {self.pasta_saida}")
        if self.memoria_pico_mb is not None:
            linhas.append(f"  Memoria (fim do lote) ......... {self.memoria_pico_mb:.0f} MB")
        linhas.append(f"  Tempo total ................... {formatar_duracao(self.duracao_seg)}")
        if self.total_processadas:
            linhas.append(
                f"  Throughput .................... {self.fotos_por_segundo:.2f} foto/s"
            )
        if self.falhas:
            linhas.append("-" * 68)
            linhas.append("  FALHAS:")
            for f in self.falhas:
                linhas.append(f"    - [{f.get('etapa')}] {f.get('arquivo')}: {f.get('motivo')}")
            omitidas = self.total_falhas - len(self.falhas)
            if omitidas > 0:
                linhas.append(f"    ... e mais {omitidas} falha(s) - veja data/_falhas/")
        linhas.append("=" * 68)
        return linhas


# ---------------------------------------------------------------------------
# Notificacao opcional (n8n / webhook)
# ---------------------------------------------------------------------------


def notificar_webhook(sumario: SumarioLote, url: str | None = None) -> bool:
    """
    Envia o sumario do lote para um webhook (ex.: n8n).

    Usa apenas a stdlib (urllib) para nao adicionar dependencia.
    Desativado por padrao: defina a variavel de ambiente
    YELLOWTRAP_WEBHOOK_URL para ligar.
    """
    logger = obter_logger(__name__)
    url = url or settings.WEBHOOK_URL
    if not url:
        return False

    import urllib.error
    import urllib.request

    corpo = json.dumps(sumario.to_dict(), ensure_ascii=False).encode("utf-8")
    requisicao = urllib.request.Request(
        url, data=corpo, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(requisicao, timeout=settings.WEBHOOK_TIMEOUT_SEG) as resp:
            logger.info("Webhook notificado (HTTP %s)", resp.status)
            return 200 <= resp.status < 300
    except Exception as exc:
        logger.warning("Falha ao notificar webhook: %s", exc)
        return False
