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
COLUNAS_GRID_INICIAL = 3  # chute inicial; corrigido no primeiro resize real
TAMANHO_PAGINA = 50  # quantos cards existem como widget ao mesmo tempo


# ---------------------------------------------------------------------------
# QScrollArea que avisa quando é redimensionada (usado pra recalcular colunas)
# ---------------------------------------------------------------------------


class _AreaProdutosScrollArea(QScrollArea):
    """
    QScrollArea comum, só que emite um sinal com a largura do VIEWPORT
    sempre que é redimensionada.

    IMPORTANTE (correção de bug): antes, a detecção de resize era feita
    no widget de CONTEÚDO (dentro da área rolável). O problema é que,
    com setWidgetResizable(True), o Qt não deixa o conteúdo encolher
    abaixo do tamanho mínimo exigido pelo QGridLayout — e esse mínimo é
    justamente "largura suficiente para as N colunas já colocadas".
    Resultado: aumentar a janela funcionava (o conteúdo cresce livre),
    mas diminuir não (o conteúdo ficava preso no mínimo da última
    quantidade de colunas, então o resizeEvent nunca chegava a reportar
    uma largura menor).

    A correção é observar o resize do VIEWPORT da QScrollArea (a área
    visível de verdade, controlada pela janela/layout externo — não pelo
    conteúdo), que encolhe e cresce livremente independente do que está
    dentro. A barra de rolagem horizontal fica desativada de propósito
    (ver _criar_area_produtos) para nunca aparecer durante o instante de
    recálculo de colunas.
    """

    redimensionado = Signal(int)  # nova largura do viewport, em pixels

    def resizeEvent(self, event) -> None:  # noqa: N802 (nome exigido pelo Qt)
        super().resizeEvent(event)
        self.redimensionado.emit(self.viewport().width())


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

        # Estado do grid responsivo: quantas colunas cabem na largura
        # atual, os cards já criados pra PÁGINA vigente (reaproveitados
        # ao redimensionar — só reposicionados, nunca recriados) e o
        # aviso de "lista vazia" quando aplicável.
        self._colunas_atuais = COLUNAS_GRID_INICIAL
        self._cards_atuais: list[OfferCard] = []
        self._aviso_vazio: QLabel | None = None

        # Estado de paginação: a lista FILTRADA completa fica em memória
        # (leve — só dados, sem widgets), mas só os produtos da página
        # atual viram OfferCard de verdade. Isso é o que resolve o app
        # ficando pesado com milhares de produtos buscados de uma vez.
        self._produtos_filtrados: list[ProdutoOferta] = []
        self._pagina_atual = 1

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

        # Referências de thread/worker precisam ser guardadas na
        # instância, senão o Python coleta o objeto e a thread morre
        # antes de terminar.
        self._thread: QThread | None = None
        self._worker: _AtualizarOfertasWorker | None = None

        self.setWindowTitle("Ofertas Shopee - Afiliados")
        self._montar_ui()
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
        layout_principal.addWidget(self._criar_area_produtos(), 1)
        layout_principal.addWidget(self._criar_rodape_paginacao())

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
        self.combo_categoria.addItem(CATEGORIA_TODAS)
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
        self.scroll_area = _AreaProdutosScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll_area.redimensionado.connect(self._ao_redimensionar_area_produtos)

        self.container_produtos = QWidget()
        self.grid_layout = QGridLayout(self.container_produtos)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.grid_layout.setContentsMargins(28, 28, 28, 28)
        self.grid_layout.setHorizontalSpacing(22)
        self.grid_layout.setVerticalSpacing(22)

        self.scroll_area.setWidget(self.container_produtos)
        return self.scroll_area

    def _calcular_colunas(self, largura_disponivel: int) -> int:
        """
        Quantas colunas de cards (largura fixa LARGURA_CARD) cabem na
        largura disponível, considerando as margens do grid e o
        espaçamento horizontal entre cards.
        """
        margens = self.grid_layout.contentsMargins()
        margens_horizontais = margens.left() + margens.right()
        espacamento = self.grid_layout.horizontalSpacing()

        largura_util = largura_disponivel - margens_horizontais + espacamento
        colunas = largura_util // (LARGURA_CARD + espacamento)
        return max(1, int(colunas))

    def _ao_redimensionar_area_produtos(self, largura: int) -> None:
        novas_colunas = self._calcular_colunas(largura)
        if novas_colunas != self._colunas_atuais:
            self._colunas_atuais = novas_colunas
            self._reposicionar_cards()

    def _criar_rodape_paginacao(self) -> QWidget:
        """
        Rodapé com navegação Anterior/Próxima — mostra só TAMANHO_PAGINA
        cards por vez, para o app continuar leve mesmo com milhares de
        produtos buscados.
        """
        container = QFrame()
        container.setObjectName("rodapePaginacao")
        container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        container.setStyleSheet(
            """
            #rodapePaginacao {
                background-color: #FFFDFE;
                border-top: 1px solid #F3D9E3;
            }
            #rodapePaginacao QLabel {
                color: #8A7680;
                font-weight: 500;
            }
            """
        )

        layout = QHBoxLayout(container)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.addStretch()

        self.botao_pagina_anterior = QPushButton("‹  Anterior")
        self.botao_pagina_anterior.setCursor(Qt.CursorShape.PointingHandCursor)
        self.botao_pagina_anterior.clicked.connect(self._ir_pagina_anterior)
        layout.addWidget(self.botao_pagina_anterior)

        self.label_pagina = QLabel("Página 1 de 1")
        self.label_pagina.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_pagina.setMinimumWidth(240)
        layout.addWidget(self.label_pagina)

        self.botao_proxima_pagina = QPushButton("Próxima  ›")
        self.botao_proxima_pagina.setCursor(Qt.CursorShape.PointingHandCursor)
        self.botao_proxima_pagina.clicked.connect(self._ir_proxima_pagina)
        layout.addWidget(self.botao_proxima_pagina)

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

        NOTA: a API só devolve productCatIds (números). Não temos, por
        enquanto, um mapeamento de categoria_id -> nome legível — o combo
        mostra os IDs mesmo. Se você tiver (ou conseguir) uma tabela de
        nomes de categoria da Shopee, dá pra plugar aqui facilmente.
        """
        selecao_atual = self.combo_categoria.currentText()

        categorias_presentes: set[int] = set()
        for produto in self.produtos:
            categorias_presentes.update(produto.categoria_ids)

        self.combo_categoria.blockSignals(True)
        self.combo_categoria.clear()
        self.combo_categoria.addItem(CATEGORIA_TODAS)
        for categoria_id in sorted(categorias_presentes):
            self.combo_categoria.addItem(str(categoria_id))

        indice_previo = self.combo_categoria.findText(selecao_atual)
        self.combo_categoria.setCurrentIndex(indice_previo if indice_previo >= 0 else 0)
        self.combo_categoria.blockSignals(False)

    # ------------------------------------------------------------------
    # Filtragem em memória + renderização do grid
    # ------------------------------------------------------------------

    def _aplicar_filtros_e_renderizar(self) -> None:
        texto_busca = self.campo_busca.text().strip().lower()
        categoria_selecionada = self.combo_categoria.currentText()
        score_minimo = self.spin_score_minimo.value()

        produtos_filtrados = []
        for produto in self.produtos:
            if texto_busca and texto_busca not in produto.nome.lower():
                continue

            if categoria_selecionada != CATEGORIA_TODAS:
                try:
                    categoria_id = int(categoria_selecionada)
                except ValueError:
                    categoria_id = None
                if categoria_id not in produto.categoria_ids:
                    continue

            if (produto.score_final or 0.0) < score_minimo:
                continue

            produtos_filtrados.append(produto)

        # Mudou o filtro -> o conjunto de resultados é outro, então volta
        # pra página 1 (uma página 7 antiga pode nem existir mais no novo
        # conjunto filtrado).
        self._produtos_filtrados = produtos_filtrados
        self._pagina_atual = 1
        self._renderizar_pagina_atual()

    # ------------------------------------------------------------------
    # Paginação da exibição (independente da paginação da busca na API)
    # ------------------------------------------------------------------

    def _total_paginas(self) -> int:
        if not self._produtos_filtrados:
            return 1
        return -(-len(self._produtos_filtrados) // TAMANHO_PAGINA)  # ceil sem importar math

    def _renderizar_pagina_atual(self) -> None:
        """
        Recorta só a fatia da página atual da lista filtrada completa e
        manda criar cards SÓ para ela — é isso que mantém o app leve
        mesmo com milhares de produtos filtrados, já que nunca existem
        mais que TAMANHO_PAGINA cards como widget ao mesmo tempo.
        """
        total_paginas = self._total_paginas()
        self._pagina_atual = max(1, min(self._pagina_atual, total_paginas))

        inicio = (self._pagina_atual - 1) * TAMANHO_PAGINA
        fim = inicio + TAMANHO_PAGINA
        produtos_da_pagina = self._produtos_filtrados[inicio:fim]

        self._renderizar_grid(produtos_da_pagina)
        self._atualizar_rodape_paginacao(total_paginas)

        # Volta o scroll pro topo ao trocar de página — sem isso, trocar
        # de página com o scroll lá embaixo deixaria o usuário perdido.
        self.scroll_area.verticalScrollBar().setValue(0)

    def _atualizar_rodape_paginacao(self, total_paginas: int) -> None:
        total_produtos = len(self._produtos_filtrados)
        if total_produtos == 0:
            self.label_pagina.setText("Nenhum produto")
        else:
            inicio = (self._pagina_atual - 1) * TAMANHO_PAGINA + 1
            fim = min(self._pagina_atual * TAMANHO_PAGINA, total_produtos)
            self.label_pagina.setText(
                f"{inicio}-{fim} de {total_produtos}  ·  "
                f"Página {self._pagina_atual} de {total_paginas}"
            )

        self.botao_pagina_anterior.setEnabled(self._pagina_atual > 1)
        self.botao_proxima_pagina.setEnabled(self._pagina_atual < total_paginas)

    def _ir_pagina_anterior(self) -> None:
        if self._pagina_atual > 1:
            self._pagina_atual -= 1
            self._renderizar_pagina_atual()

    def _ir_proxima_pagina(self) -> None:
        if self._pagina_atual < self._total_paginas():
            self._pagina_atual += 1
            self._renderizar_pagina_atual()

    def _renderizar_grid(self, produtos: list[ProdutoOferta]) -> None:
        """
        Reconstrói o conjunto de cards do zero — chamado quando o filtro
        (busca/categoria/score) muda, ou seja, quando o CONJUNTO de
        produtos exibidos realmente é outro. Redimensionar a janela NÃO
        passa por aqui (ver _reposicionar_cards).
        """
        self._limpar_grid()

        if not produtos:
            self._aviso_vazio = QLabel(
                "Nenhum produto encontrado para os filtros atuais."
            )
            self._aviso_vazio.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._aviso_vazio.setStyleSheet(
                "color: #C7A7B4; font-size: 12pt; padding: 40px;"
            )
            self._cards_atuais = []
            self._reposicionar_cards()
            return

        self._cards_atuais = [self._criar_card(produto) for produto in produtos]
        self._reposicionar_cards()

    def _reposicionar_cards(self) -> None:
        """
        Rearranja os widgets JÁ EXISTENTES (cards ou o aviso de lista
        vazia) no grid, de acordo com self._colunas_atuais — sem recriar
        nada. É isso que roda a cada redimensionamento da janela: barato
        (só reposiciona), diferente de _renderizar_grid (que recria os
        cards e dispararia novos carregamentos de imagem à toa).

        QGridLayout.takeAt() remove o item do LAYOUT, mas não deleta o
        widget — é por isso que os cards sobrevivem a essa operação.
        """
        while self.grid_layout.count():
            self.grid_layout.takeAt(0)

        if self._aviso_vazio is not None:
            self.grid_layout.addWidget(
                self._aviso_vazio, 0, 0, 1, self._colunas_atuais
            )
            return

        for indice, card in enumerate(self._cards_atuais):
            linha = indice // self._colunas_atuais
            coluna = indice % self._colunas_atuais
            self.grid_layout.addWidget(card, linha, coluna)

    def _limpar_grid(self) -> None:
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cards_atuais = []
        self._aviso_vazio = None

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

    # ------------------------------------------------------------------
    # Encerramento seguro
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        if self._thread is not None and self._thread.isRunning():
            logger.info(
                "Fechando com atualização em andamento — aguardando thread encerrar."
            )
            self._thread.requestInterruption()
            self._thread.quit()
            if not self._thread.wait(5000):
                logger.warning("Thread não encerrou em 5s — terminate() forçado.")
                self._thread.terminate()
                self._thread.wait()
        event.accept()
