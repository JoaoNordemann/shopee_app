"""
main_window.py

Janela principal do app (PySide6). Responsável por:
    - Cabeçalho: botão "Atualizar", indicador de carregamento, busca por
      texto, filtro de categoria e filtro de score mínimo.
    - Disparar OfertaService.atualizar_ofertas() numa QThread separada
      (a UI não pode travar durante a busca na API).
    - Ao abrir, tentar OfertaService.carregar_do_cache() primeiro (app
      abre rápido); só dispara atualização automática se não houver
      cache válido.
    - Grid rolável com um OfferCard por produto (imagem, dados, score,
      botões de copiar e edição manual de utilidade/facilidade).
    - Filtrar (busca/categoria/score mínimo) em memória, sem chamar a
      API de novo — os filtros atuam sobre a última lista carregada.
    - Banner de erro visível se a atualização falhar.

Este arquivo não cria QApplication nem chama .show() — isso é
responsabilidade do main.py (próximo arquivo). MainWindow recebe uma
instância de OfertaService já pronta via construtor, para facilitar
testes e não acoplar esta classe à forma como as credenciais/config são
carregadas.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import config
from data_service import OfertaService, OfertaServiceError
from offer_card import LARGURA_CARD, OfferCard
from scoring import ProdutoOferta

logger = logging.getLogger(__name__)

CATEGORIA_TODAS = "Todas"
COLUNAS_GRID = 3

# Quantos cards são efetivamente criados/renderizados de uma vez. Com
# 2500+ produtos, criar um OfferCard por item (sombra + spinboxes +
# download de imagem em thread) para TODOS de uma vez é o que estava
# deixando a UI pesada. A paginação limita o grid a uma "página" por
# vez — os filtros continuam rodando sobre a lista completa em memória,
# só a criação de widgets é que passa a ser limitada.
PRODUTOS_POR_PAGINA = 60

# Usados tanto para montar o grid quanto para calcular a largura mínima
# da janela logo abaixo — mantidos como constantes nomeadas (em vez de
# números soltos em dois lugares) justamente para os dois cálculos nunca
# ficarem dessincronizados.
MARGEM_GRID = 28
ESPACAMENTO_HORIZONTAL_GRID = 22

# Largura mínima/máxima da janela: trava exatamente no ponto que garante
# as 3 colunas completas (nunca menos), sem permitir que o usuário
# arraste a borda e deixe os cards cortados. Some margens do grid + os
# espaçamentos entre colunas + a largura de cada card + uma folga para a
# scrollbar vertical (10px de largura, ver ESTILO_APP em main.py) e para
# a borda/moldura da própria janela do SO.
FOLGA_SCROLLBAR_E_MOLDURA = 24
LARGURA_JANELA_FIXA = (
    MARGEM_GRID * 2
    + LARGURA_CARD * COLUNAS_GRID
    + ESPACAMENTO_HORIZONTAL_GRID * (COLUNAS_GRID - 1)
    + FOLGA_SCROLLBAR_E_MOLDURA
)


# ---------------------------------------------------------------------------
# Worker (roda em QThread separada)
# ---------------------------------------------------------------------------


class _AtualizarOfertasWorker(QObject):
    """
    Executa OfertaService.atualizar_ofertas() fora da thread principal.
    Vive numa QThread própria (ver MainWindow._iniciar_atualizacao) para
    que a chamada de rede não trave a interface.
    """

    concluido = Signal(list)  # list[ProdutoOferta]
    falhou = Signal(str)

    def __init__(self, oferta_service: OfertaService):
        super().__init__()
        self.oferta_service = oferta_service

    def run(self) -> None:
        try:
            produtos = self.oferta_service.atualizar_ofertas()
        except OfertaServiceError as exc:
            logger.error("Falha ao atualizar ofertas: %s", exc)
            self.falhou.emit(str(exc))
        except Exception:
            logger.exception("Erro inesperado ao atualizar ofertas.")
            self.falhou.emit(
                "Ocorreu um erro inesperado ao atualizar as ofertas."
            )
        else:
            self.concluido.emit(produtos)


# ---------------------------------------------------------------------------
# Janela principal
# ---------------------------------------------------------------------------


class MainWindow(QWidget):
    def __init__(self, oferta_service: OfertaService, parent: QWidget | None = None):
        super().__init__(parent)
        self.oferta_service = oferta_service

        # Última lista completa carregada (da API ou do cache). Os
        # filtros trabalham sempre em cima desta lista, em memória.
        self.produtos: list[ProdutoOferta] = []

        # Resultado do último filtro aplicado (busca/categoria/score) e a
        # página atual dentro desse resultado. self.produtos pode ter
        # milhares de itens; produtos_filtrados também pode — só a fatia
        # da página atual vira widgets de fato (ver _renderizar_pagina_atual).
        self.produtos_filtrados: list[ProdutoOferta] = []
        self.pagina_atual: int = 0

        # Repassados para cada OfferCard: a lista de templates de legenda
        # (botão "Copiar texto" sorteia um a cada clique) e as fontes
        # auxiliares de score (reputação, risco, tabelas por categoria)
        # que o OfertaService já mantém, para o recálculo manual de
        # Utilidade/Facilidade no card usar exatamente os mesmos dados
        # da última atualização geral.
        self.templates_legenda = config.TEMPLATES_TEXTO_WHATSAPP
        self.fontes_auxiliares = {
            "lista_lojas_confiaveis": oferta_service.lista_lojas_confiaveis,
            "lista_garantias": oferta_service.lista_garantias,
            "tabela_utilidade_por_categoria": oferta_service.tabela_utilidade_por_categoria,
            "tabela_facilidade_por_categoria": oferta_service.tabela_facilidade_por_categoria,
        }
        # Mapeamento categoria_id -> nome (ver data_service.py). Categorias
        # sem entrada aqui continuam aparecendo no filtro, só que como
        # "Categoria {id}" em vez de um nome legível.
        self.nomes_categorias = oferta_service.nomes_categorias

        # Referências de thread/worker precisam ser guardadas na
        # instância, senão o Python coleta o objeto e a thread morre
        # antes de terminar.
        self._thread: QThread | None = None
        self._worker: _AtualizarOfertasWorker | None = None

        self.setWindowTitle("Ofertas Shopee - Afiliados")
        self._montar_ui()

        # Trava a LARGURA da janela em LARGURA_JANELA_FIXA (mínimo == máximo),
        # o suficiente para as 3 colunas completas de cards — o usuário não
        # consegue mais arrastar a borda e deixar cards cortados. A ALTURA
        # continua livre: só setamos min/max de largura, não de altura.
        self.setMinimumWidth(LARGURA_JANELA_FIXA)
        self.setMaximumWidth(LARGURA_JANELA_FIXA)

        self._carregar_estado_inicial()

    # ------------------------------------------------------------------
    # Montagem da UI
    # ------------------------------------------------------------------

    def _montar_ui(self) -> None:
        layout_principal = QVBoxLayout(self)
        layout_principal.setContentsMargins(0, 0, 0, 0)
        layout_principal.setSpacing(0)

        layout_principal.addWidget(self._criar_banner_erro())
        layout_principal.addWidget(self._criar_cabecalho())
        layout_principal.addWidget(self._criar_area_produtos())
        layout_principal.addWidget(self._criar_barra_paginacao())

    def _criar_banner_erro(self) -> QWidget:
        self.banner_erro = QLabel("")
        self.banner_erro.setWordWrap(True)
        self.banner_erro.setContentsMargins(14, 10, 14, 10)
        self.banner_erro.setStyleSheet(
            "background-color: #FDE7EC; color: #B23A55; padding: 8px; "
            "border: none; border-bottom: 1px solid #F5C2CE; "
            "font-weight: 500;"
        )
        self.banner_erro.setVisible(False)
        return self.banner_erro

    def _criar_cabecalho(self) -> QWidget:
        """
        Barra de cabeçalho clara ("glass" leve): mesma família de cor do
        fundo do app, separada apenas por uma sombra suave e uma borda
        muito fina — visual arejado, sem contraste pesado.
        """
        container = QFrame()
        container.setObjectName("headerBar")
        container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        container.setStyleSheet(
            """
            #headerBar {
                background-color: #FFFDFE;
                border-bottom: 1px solid #F3D9E3;
            }
            #headerBar QLabel {
                color: #8A7680;
                font-weight: 500;
            }
            """
        )
        sombra = QGraphicsDropShadowEffect(container)
        sombra.setBlurRadius(16)
        sombra.setXOffset(0)
        sombra.setYOffset(3)
        sombra.setColor(QColor(255, 182, 193, 60))
        container.setGraphicsEffect(sombra)

        layout = QHBoxLayout(container)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(10)

        # Logo minimalista: círculo rosa pastel com um emoji de sacola,
        # dando identidade visual ao app sem precisar de arquivo de
        # ícone externo.
        logo = QLabel("🛍")
        logo.setFixedSize(34, 34)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setStyleSheet(
            "background-color: #FFE1E9; border-radius: 17px; font-size: 15pt;"
        )
        layout.addWidget(logo)
        layout.addSpacing(6)

        self.botao_atualizar = QPushButton("↻  Atualizar")
        self.botao_atualizar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.botao_atualizar.clicked.connect(self._iniciar_atualizacao)
        layout.addWidget(self.botao_atualizar)

        # Indicador de carregamento (spinner indeterminado + texto).
        self.progresso = QProgressBar()
        self.progresso.setRange(0, 0)  # modo indeterminado ("busy")
        self.progresso.setFixedSize(80, 8)
        self.progresso.setTextVisible(False)
        self.progresso.setVisible(False)
        layout.addWidget(self.progresso)

        self.label_carregando = QLabel("Buscando ofertas...")
        self.label_carregando.setVisible(False)
        layout.addWidget(self.label_carregando)

        layout.addSpacing(20)

        layout.addWidget(QLabel("🔍 Buscar:"))
        self.campo_busca = QLineEdit()
        self.campo_busca.setPlaceholderText("Nome do produto...")
        self.campo_busca.setMinimumWidth(200)
        self.campo_busca.textChanged.connect(self._aplicar_filtros_e_renderizar)
        layout.addWidget(self.campo_busca)

        layout.addWidget(QLabel("🏷 Categoria:"))
        self.combo_categoria = QComboBox()
        self.combo_categoria.addItem(CATEGORIA_TODAS, None)
        self.combo_categoria.currentIndexChanged.connect(
            self._aplicar_filtros_e_renderizar
        )
        layout.addWidget(self.combo_categoria)

        layout.addWidget(QLabel("⭐ Score mínimo:"))
        self.spin_score_minimo = QDoubleSpinBox()
        self.spin_score_minimo.setRange(0.0, 10.0)
        self.spin_score_minimo.setSingleStep(0.5)
        self.spin_score_minimo.setDecimals(1)
        self.spin_score_minimo.setValue(0.0)
        self.spin_score_minimo.valueChanged.connect(self._aplicar_filtros_e_renderizar)
        layout.addWidget(self.spin_score_minimo)

        layout.addStretch()
        return container

    def _criar_area_produtos(self) -> QScrollArea:
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)

        self.container_produtos = QWidget()
        self.grid_layout = QGridLayout(self.container_produtos)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.grid_layout.setContentsMargins(
            MARGEM_GRID, MARGEM_GRID, MARGEM_GRID, MARGEM_GRID
        )
        self.grid_layout.setHorizontalSpacing(ESPACAMENTO_HORIZONTAL_GRID)
        self.grid_layout.setVerticalSpacing(22)

        self.scroll_area.setWidget(self.container_produtos)
        return self.scroll_area

    def _criar_barra_paginacao(self) -> QWidget:
        """
        Barra fixa embaixo do grid: "‹ Anterior | Página X de Y (N produtos) | Próxima ›".
        Mesma família visual do cabeçalho (fundo claro + borda fina).
        """
        container = QFrame()
        container.setObjectName("barraPaginacao")
        container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        container.setStyleSheet(
            """
            #barraPaginacao {
                background-color: #FFFDFE;
                border-top: 1px solid #F3D9E3;
            }
            """
        )

        layout = QHBoxLayout(container)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(14)

        layout.addStretch()

        self.botao_pagina_anterior = QPushButton("‹  Anterior")
        self.botao_pagina_anterior.setCursor(Qt.CursorShape.PointingHandCursor)
        self.botao_pagina_anterior.clicked.connect(self._ir_pagina_anterior)
        layout.addWidget(self.botao_pagina_anterior)

        self.label_pagina_info = QLabel("Página 1 de 1")
        self.label_pagina_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_pagina_info.setMinimumWidth(220)
        self.label_pagina_info.setStyleSheet("color: #8A7680; font-weight: 500;")
        layout.addWidget(self.label_pagina_info)

        self.botao_pagina_proxima = QPushButton("Próxima  ›")
        self.botao_pagina_proxima.setCursor(Qt.CursorShape.PointingHandCursor)
        self.botao_pagina_proxima.clicked.connect(self._ir_pagina_seguinte)
        layout.addWidget(self.botao_pagina_proxima)

        layout.addStretch()
        return container

    # ------------------------------------------------------------------
    # Carregamento inicial (cache-first)
    # ------------------------------------------------------------------

    def _carregar_estado_inicial(self) -> None:
        cache = self.oferta_service.carregar_do_cache()
        if cache:
            logger.info("Abrindo com %d produtos do cache.", len(cache))
            self.produtos = cache
            self._atualizar_combo_categorias()
            self._aplicar_filtros_e_renderizar()
        else:
            logger.info("Sem cache válido — disparando atualização inicial.")
            self._iniciar_atualizacao()

    # ------------------------------------------------------------------
    # Atualização (QThread)
    # ------------------------------------------------------------------

    def _iniciar_atualizacao(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            logger.info("Atualização já em andamento — ignorando novo clique.")
            return

        self._ocultar_banner_erro()
        self.botao_atualizar.setEnabled(False)
        self.progresso.setVisible(True)
        self.label_carregando.setVisible(True)

        self._thread = QThread()
        self._worker = _AtualizarOfertasWorker(self.oferta_service)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.concluido.connect(self._ao_atualizar_sucesso)
        self._worker.falhou.connect(self._ao_atualizar_falha)
        self._worker.concluido.connect(self._thread.quit)
        self._worker.falhou.connect(self._thread.quit)
        self._thread.finished.connect(self._ao_finalizar_thread)

        self._thread.start()

    def _ao_atualizar_sucesso(self, produtos: list[ProdutoOferta]) -> None:
        logger.info("Atualização concluída: %d produtos.", len(produtos))
        self.produtos = produtos
        self._atualizar_combo_categorias()
        self._aplicar_filtros_e_renderizar()

    def _ao_atualizar_falha(self, mensagem: str) -> None:
        self._mostrar_banner_erro(mensagem)

    def _ao_finalizar_thread(self) -> None:
        self.botao_atualizar.setEnabled(True)
        self.progresso.setVisible(False)
        self.label_carregando.setVisible(False)

        # Limpeza: agenda a destruição de worker/thread e solta as
        # referências para não segurar objetos Qt já finalizados.
        if self._worker is not None:
            self._worker.deleteLater()
        if self._thread is not None:
            self._thread.deleteLater()
        self._worker = None
        self._thread = None

    # ------------------------------------------------------------------
    # Banner de erro
    # ------------------------------------------------------------------

    def _mostrar_banner_erro(self, mensagem: str) -> None:
        self.banner_erro.setText(mensagem)
        self.banner_erro.setVisible(True)

    def _ocultar_banner_erro(self) -> None:
        self.banner_erro.setVisible(False)
        self.banner_erro.setText("")

    # ------------------------------------------------------------------
    # Filtro de categoria (combo dinâmico)
    # ------------------------------------------------------------------

    def _atualizar_combo_categorias(self) -> None:
        """
        Repopula o combo de categorias a partir das categorias realmente
        presentes em self.produtos. Tenta preservar a seleção atual, se
        ela ainda existir na nova lista.

        A API só devolve productCatIds (números) — não existe nome de
        categoria vindo da Shopee. Por isso usamos self.nomes_categorias
        (mapeamento local, ver data_service.py) para mostrar um nome
        legível quando disponível; categorias ainda não mapeadas caem no
        fallback "Categoria {id}". Em qualquer caso, o ID de verdade fica
        guardado como itemData do combo (não no texto), então a
        filtragem nunca depende de parsear o texto exibido.
        """
        selecao_atual = self.combo_categoria.currentData()

        categorias_presentes: set[int] = set()
        for produto in self.produtos:
            categorias_presentes.update(produto.categoria_ids)

        self.combo_categoria.blockSignals(True)
        self.combo_categoria.clear()
        self.combo_categoria.addItem(CATEGORIA_TODAS, None)
        for categoria_id in sorted(categorias_presentes):
            nome = self.nomes_categorias.get(categoria_id)
            texto = f"{nome} ({categoria_id})" if nome else f"Categoria {categoria_id}"
            self.combo_categoria.addItem(texto, categoria_id)

        indice_previo = self.combo_categoria.findData(selecao_atual)
        self.combo_categoria.setCurrentIndex(indice_previo if indice_previo >= 0 else 0)
        self.combo_categoria.blockSignals(False)

    # ------------------------------------------------------------------
    # Filtragem em memória + renderização do grid
    # ------------------------------------------------------------------

    def _aplicar_filtros_e_renderizar(self) -> None:
        texto_busca = self.campo_busca.text().strip().lower()
        categoria_id_selecionada = self.combo_categoria.currentData()  # None = "Todas"
        score_minimo = self.spin_score_minimo.value()

        produtos_filtrados = []
        for produto in self.produtos:
            if texto_busca and texto_busca not in produto.nome.lower():
                continue

            if (
                categoria_id_selecionada is not None
                and categoria_id_selecionada not in produto.categoria_ids
            ):
                continue

            if (produto.score_final or 0.0) < score_minimo:
                continue

            produtos_filtrados.append(produto)

        self.produtos_filtrados = produtos_filtrados
        # Qualquer mudança de filtro volta para a primeira página — não
        # faria sentido manter "página 8" de um filtro que agora só tem
        # 2 produtos.
        self.pagina_atual = 0
        self._renderizar_pagina_atual()

    # ------------------------------------------------------------------
    # Paginação
    # ------------------------------------------------------------------

    def _total_paginas(self) -> int:
        if not self.produtos_filtrados:
            return 1
        return -(-len(self.produtos_filtrados) // PRODUTOS_POR_PAGINA)  # ceil

    def _ir_pagina_anterior(self) -> None:
        if self.pagina_atual > 0:
            self.pagina_atual -= 1
            self._renderizar_pagina_atual()

    def _ir_pagina_seguinte(self) -> None:
        if self.pagina_atual < self._total_paginas() - 1:
            self.pagina_atual += 1
            self._renderizar_pagina_atual()

    def _renderizar_pagina_atual(self) -> None:
        """
        Fatia produtos_filtrados na página atual e só então cria os
        OfferCard dessa fatia — é isso que limita o número de widgets
        vivos ao mesmo tempo (PRODUTOS_POR_PAGINA), em vez de recriar um
        card para cada um dos milhares de produtos filtrados.
        """
        total_paginas = self._total_paginas()
        self.pagina_atual = max(0, min(self.pagina_atual, total_paginas - 1))

        inicio = self.pagina_atual * PRODUTOS_POR_PAGINA
        fim = inicio + PRODUTOS_POR_PAGINA
        pagina = self.produtos_filtrados[inicio:fim]

        self._renderizar_grid(pagina)

        total_produtos = len(self.produtos_filtrados)
        self.label_pagina_info.setText(
            f"Página {self.pagina_atual + 1} de {total_paginas} "
            f"({total_produtos} produtos)"
        )
        self.botao_pagina_anterior.setEnabled(self.pagina_atual > 0)
        self.botao_pagina_proxima.setEnabled(self.pagina_atual < total_paginas - 1)

        # Volta o scroll pro topo a cada troca de página — sem isso, ao
        # ir para a página 2, o usuário abriria já rolado como se ainda
        # estivesse na posição de scroll da página anterior.
        self.scroll_area.verticalScrollBar().setValue(0)

    def _renderizar_grid(self, produtos: list[ProdutoOferta]) -> None:
        self._limpar_grid()

        if not produtos:
            aviso = QLabel("Nenhum produto encontrado para os filtros atuais.")
            aviso.setAlignment(Qt.AlignmentFlag.AlignCenter)
            aviso.setStyleSheet("color: #C7A7B4; font-size: 12pt; padding: 40px;")
            self.grid_layout.addWidget(aviso, 0, 0, 1, COLUNAS_GRID)
            return

        for indice, produto in enumerate(produtos):
            linha = indice // COLUNAS_GRID
            coluna = indice % COLUNAS_GRID
            card = self._criar_card(produto)
            self.grid_layout.addWidget(card, linha, coluna)

    def _limpar_grid(self) -> None:
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _criar_card(self, produto: ProdutoOferta) -> QWidget:
        return OfferCard(
            produto=produto,
            templates_legenda=self.templates_legenda,
            fontes_auxiliares=self.fontes_auxiliares,
            on_score_changed=self._ao_score_de_card_alterado,
        )

    def _ao_score_de_card_alterado(self, produto: ProdutoOferta) -> None:
        # O produto é o mesmo objeto (mesma referência) que está em
        # self.produtos, então o novo score já fica refletido ali sem
        # nenhuma ação extra. Decisão de design: NÃO re-renderizamos o
        # grid inteiro aqui, para o card não "sumir" da tela no meio da
        # edição caso o novo score fique fora do filtro de score mínimo
        # — o filtro só é reaplicado na próxima busca/categoria/score
        # alterados manualmente ou na próxima atualização geral.
        pass
