"""
Contrato de entrada do pipeline.

Este modulo existe para que o pipeline possa ser embutido em outro sistema
(API, fila, n8n, CLI) sem que o chamador precise conhecer a ordem dos
argumentos de meia duzia de funcoes: monta-se um `OpcoesProcessamento`,
valida-se uma vez e o objeto atravessa todas as etapas.

    from src.opcoes import OpcoesProcessamento
    from src.pipeline import executar_processamento

    opcoes = OpcoesProcessamento(modo="sequencial", pasta_entrada="D:/envio_42")
    sumario = executar_processamento(opcoes)

Todos os campos tem default vindo de config/settings.py: quem nao quer
escolher nada roda `OpcoesProcessamento()` e recebe o comportamento padrao.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from config import settings

__all__ = ["OpcoesProcessamento"]


def _ou(valor, padrao):
    """Devolve `padrao` quando o campo veio como None."""
    return padrao if valor is None else valor


@dataclass
class OpcoesProcessamento:
    """
    Tudo o que define UMA execucao do pipeline.

    Campos deixados como None assumem o default de settings.py no
    __post_init__ - depois disso o objeto esta completamente resolvido e pode
    ser serializado (to_dict) para log, fila ou banco.
    """

    # --- o que rodar ---
    modo: str = settings.MODO_PADRAO

    # --- de onde e para onde ---
    pasta_entrada: Path | None = None
    pasta_renomeadas: Path | None = None
    pasta_recortadas: Path | None = None
    lote_id: str | None = None

    # --- nomeacao ---
    estrategia_renomeacao: str | None = None
    verificar_md5: bool | None = None
    criar_zip: bool | None = None
    limpar_destino_renomeadas: bool | None = None
    prefixo: str | None = None
    digitos: int | None = None
    indice_inicial: int | None = None
    continuar_numeracao: bool | None = None

    # --- recorte ---
    formato: str | None = None
    perfil: str | None = None          # cor da armadilha: auto | amarela | azul
    pular_existentes: bool | None = None
    limpar_saida: bool | None = None

    # --- execucao ---
    workers: int | None = None
    workers_io: int | None = None
    limite: int | None = None          # processa apenas as N primeiras fotos
    simular: bool = False              # monta o plano e nao grava nada
    notificar: bool | None = None
    reter_resultados: bool | None = None  # None = decide pelo tamanho do lote

    # --- metadados livres (o sistema chamador pode carimbar o que quiser) ---
    contexto: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Resolucao e validacao
    # ------------------------------------------------------------------

    def __post_init__(self) -> None:
        self.modo = str(self.modo).strip().lower()
        if self.modo not in settings.MODOS_VALIDOS:
            raise ValueError(
                f"Modo invalido: {self.modo!r}. "
                f"Validos: {', '.join(settings.MODOS_VALIDOS)}"
            )

        self.pasta_entrada = Path(_ou(self.pasta_entrada, settings.PASTA_ENTRADA))
        self.pasta_renomeadas = Path(_ou(self.pasta_renomeadas,
                                         settings.PASTA_RENOMEADAS))
        self.pasta_recortadas = Path(_ou(self.pasta_recortadas,
                                         settings.PASTA_RECORTADAS))

        # Lembra se a estrategia foi escolhida a mao: em `com(modo=...)` uma
        # estrategia herdada por default precisa acompanhar o novo modo, mas
        # uma escolhida explicitamente tem que ser respeitada.
        self._estrategia_explicita = self.estrategia_renomeacao is not None
        self.estrategia_renomeacao = str(_ou(
            self.estrategia_renomeacao,
            settings.RENOMEACAO_ESTRATEGIA.get(
                self.modo, settings.RENOMEACAO_ESTRATEGIA_PADRAO),
        )).strip().lower()
        if self.estrategia_renomeacao not in settings.ESTRATEGIAS_VALIDAS:
            raise ValueError(
                f"Estrategia de renomeacao invalida: {self.estrategia_renomeacao!r}. "
                f"Validas: {', '.join(settings.ESTRATEGIAS_VALIDAS)}"
            )

        self.verificar_md5 = bool(_ou(self.verificar_md5,
                                      settings.RENOMEACAO_VERIFICAR_MD5))
        self.criar_zip = bool(_ou(self.criar_zip, settings.RENOMEACAO_CRIAR_ZIP))
        self.limpar_destino_renomeadas = bool(_ou(
            self.limpar_destino_renomeadas, settings.RENOMEACAO_LIMPAR_DESTINO))

        self.prefixo = str(_ou(self.prefixo, settings.SEQUENCIAL_PREFIXO))
        self.digitos = int(_ou(self.digitos, settings.SEQUENCIAL_DIGITOS))
        self.indice_inicial = int(_ou(self.indice_inicial, settings.SEQUENCIAL_INICIO))
        self.continuar_numeracao = bool(_ou(
            self.continuar_numeracao, settings.SEQUENCIAL_CONTINUAR_NUMERACAO))
        if self.digitos < 1:
            raise ValueError("digitos precisa ser >= 1")
        if self.indice_inicial < 0:
            raise ValueError("indice_inicial precisa ser >= 0")

        self.formato = str(_ou(self.formato, settings.RECORTE_FORMATO_SAIDA))
        if self.formato not in settings.RECORTE_FORMATOS_VALIDOS:
            raise ValueError(
                f"Formato invalido: {self.formato!r}. "
                f"Validos: {', '.join(settings.RECORTE_FORMATOS_VALIDOS)}"
            )
        self.perfil = str(_ou(self.perfil,
                              settings.RECORTE_PERFIL_PADRAO)).strip().lower()
        if self.perfil not in settings.RECORTE_PERFIS:
            raise ValueError(
                f"Perfil de armadilha invalido: {self.perfil!r}. "
                f"Validos: {', '.join(settings.RECORTE_PERFIS)}"
            )
        self.pular_existentes = bool(_ou(self.pular_existentes,
                                         settings.RECORTE_PULAR_EXISTENTES))
        self.limpar_saida = bool(_ou(self.limpar_saida,
                                     settings.LIMPAR_PASTAS_INTERMEDIARIAS))
        self.notificar = bool(_ou(self.notificar, settings.WEBHOOK_ATIVO))

        if self.limite is not None:
            self.limite = int(self.limite)
            if self.limite <= 0:
                raise ValueError("limite precisa ser > 0 (ou None)")
        if self.workers is not None:
            self.workers = max(1, int(self.workers))
        if self.workers_io is not None:
            self.workers_io = max(1, int(self.workers_io))

        if self.limpar_saida and self.pular_existentes:
            raise ValueError(
                "limpar_saida e pular_existentes se anulam: a limpeza apaga "
                "exatamente os arquivos que a retomada usaria."
            )

    # ------------------------------------------------------------------
    # Propriedades derivadas
    # ------------------------------------------------------------------

    @property
    def materializa_renomeadas(self) -> bool:
        """True quando a pasta 02_renomeadas e realmente escrita."""
        return self.estrategia_renomeacao != settings.ESTRATEGIA_VIRTUAL

    @property
    def duplica_em_disco(self) -> bool:
        """
        True quando o lote passa a existir DUAS vezes em disco.

        So 'copiar' e 'hardlink' criam um segundo arquivo por foto em
        02_renomeadas; 'mover' apenas realoca e 'virtual' (padrao) nao
        escreve nada la.
        """
        return self.estrategia_renomeacao in (settings.ESTRATEGIA_COPIAR,
                                              settings.ESTRATEGIA_HARDLINK)

    @property
    def renomeia(self) -> bool:
        """False no modo 'recorte' (nome de origem preservado)."""
        return self.modo != settings.MODO_RECORTE

    @property
    def pasta_origem_do_recorte(self) -> Path:
        """De onde o recorte le as fotos, dado o modo e a estrategia."""
        if self.renomeia and self.materializa_renomeadas:
            return self.pasta_renomeadas
        return self.pasta_entrada

    def resolver_workers_io(self) -> int:
        """Threads da etapa de materializacao (I/O-bound)."""
        if self.workers_io:
            return self.workers_io
        if settings.RENOMEACAO_WORKERS_IO:
            return int(settings.RENOMEACAO_WORKERS_IO)
        return max(1, min(8, (os.cpu_count() or 2) * 2))

    # ------------------------------------------------------------------
    # Serializacao / clonagem
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        dados = asdict(self)
        for chave, valor in dados.items():
            if isinstance(valor, Path):
                dados[chave] = str(valor)
        return dados

    def com(self, **alteracoes: Any) -> "OpcoesProcessamento":
        """
        Clone com campos trocados (revalida tudo).

        Trocar o modo sem dizer nada sobre a estrategia faz o clone adotar a
        estrategia default do novo modo - do contrario uma estrategia herdada
        por acaso (e que pode duplicar o lote em disco) atravessaria a troca
        de modo sem ninguem ter pedido.
        """
        troca_modo = "modo" in alteracoes and alteracoes["modo"] != self.modo
        if (troca_modo and "estrategia_renomeacao" not in alteracoes
                and not self._estrategia_explicita):
            alteracoes["estrategia_renomeacao"] = None
        novo = replace(self, **alteracoes)
        novo._estrategia_explicita = (self._estrategia_explicita
                                      or "estrategia_renomeacao" in alteracoes)
        return novo

    def linhas_resumo(self) -> list[str]:
        """Uma linha por parametro relevante, para o cabecalho do log."""
        linhas = [
            f"  Modo ........................ {self.modo}",
            f"  Entrada ..................... {self.pasta_entrada}",
            f"  Saida (recortes) ............ {self.pasta_recortadas}",
            f"  Formato do recorte .......... {self.formato}",
            f"  Armadilha ................... {self.perfil}"
            + (" (amarela e azul)" if self.perfil == "auto" else ""),
        ]
        if self.renomeia:
            if self.modo == settings.MODO_SEQUENCIAL:
                exemplo = f"{self.prefixo}{self.indice_inicial:0{self.digitos}d}"
                linhas.append(f"  Nomeacao .................... sequencial ({exemplo}...)")
            else:
                linhas.append("  Nomeacao .................... grid a1..d10")
            linhas.append(f"  Materializar renomeadas ..... {self.estrategia_renomeacao}")
            if self.estrategia_renomeacao == settings.ESTRATEGIA_COPIAR:
                linhas.append(f"  Conferencia MD5 ............. {self.verificar_md5}")
        else:
            linhas.append("  Nomeacao .................... preserva o nome de origem")
        if self.duplica_em_disco:
            linhas.append("  Arquivos por foto ........... 2 (copia intermediaria "
                          "+ quadrante)")
        else:
            linhas.append("  Arquivos por foto ........... 1 (apenas o quadrante)")
        if self.limite:
            linhas.append(f"  Limite ...................... {self.limite} foto(s)")
        if self.pular_existentes:
            linhas.append("  Retomada .................... pula quadrantes ja existentes")
        if self.simular:
            linhas.append("  SIMULACAO ................... nada sera gravado")
        return linhas
