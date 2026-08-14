"""
YellowTrap Pipeline - processamento de imagens de armadilhas amarelas.

Grupo Progresso / Setor de Inovacao.

Modulos:
    renomeacao  - Protocolo 1: renomeia para o grid a1..d10 com conferencia MD5
    recorte     - Protocolo 2: recorta o quadrante central (deteccao de grade)
    stitching   - Protocolo 3: monta a placa em layout horizontal 4 x 10
    exportacao  - salvamento em multiplos formatos/resolucoes
    paralelismo - wrapper de ProcessPoolExecutor
    pipeline    - orquestracao das 3 etapas
    utils       - logging, medicao de recursos, registro de falhas
"""

__version__ = "1.0.0"
