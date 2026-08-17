"""
YellowTrap Pipeline - processamento de imagens de armadilhas amarelas.

Grupo Progresso / Setor de Inovacao.

O pipeline tem duas etapas e termina no recorte dos quadrantes.

Modulos:
    opcoes      - OpcoesProcessamento: o contrato de entrada de uma execucao
    renomeacao  - Etapa 1: plano de nomes (grid a1..d10 ou VARD0) e sua
                  materializacao (virtual / hardlink / copiar+MD5 / mover)
    recorte     - Etapa 2: recorta o quadrante central (deteccao de grade)
    exportacao  - gravacao dos quadrantes (PNG / TIFF / JPEG)
    paralelismo - wrapper de ProcessPoolExecutor com submissao em janela
    pipeline    - orquestracao das duas etapas
    utils       - logging, medicao de recursos, registro de falhas, sumario
"""

__version__ = "2.0.0"
