"""
Painel ephemeral de administração do banco (Components V2).

Sem timeout: fica aberto até o administrador fechar ou a mensagem ephemeral
expirar no Discord. Navegação por selects e botões; edição/inserção via modal.
"""

from __future__ import annotations

import logging

import discord

from src.banco.banco_service import (
    LINHAS_POR_PAGINA,
    apagar_linha_por_pk,
    atualizar_campo,
    buscar_linha_por_pk,
    codificar_pk,
    contar_linhas,
    decodificar_pk,
    inserir_linha,
    listar_chaves_primarias,
    listar_colunas,
    listar_linhas,
    listar_nomes_das_tabelas,
    pk_da_linha,
    resumo_da_linha,
    tabela_e_conhecida,
)
from src.utils.error_handling import (
    LoggingModalMixin,
    LoggingViewMixin,
)
from src.utils.mensagens import (
    editar_mensagem_original,
    responder_erro,
)

registrador = logging.getLogger(__name__)

CUSTOM_SELECT_TABELA = "banco:sel_tabela"
CUSTOM_SELECT_LINHA = "banco:sel_linha"
CUSTOM_SELECT_CAMPO = "banco:sel_campo"
CUSTOM_BTN_ANTERIOR = "banco:pag_ant"
CUSTOM_BTN_PROXIMA = "banco:pag_prox"
CUSTOM_BTN_ATUALIZAR = "banco:atualizar"
CUSTOM_BTN_INSERIR = "banco:inserir"
CUSTOM_BTN_APAGAR = "banco:apagar"
CUSTOM_BTN_EDITAR = "banco:editar"
CUSTOM_BTN_VOLTAR = "banco:voltar"


def _membro_e_admin(membro: discord.Member | discord.User) -> bool:
    if not isinstance(membro, discord.Member):
        return False
    return bool(membro.guild_permissions.administrator)


class PainelBancoView(LoggingViewMixin, discord.ui.LayoutView):
    """
    Painel principal — lista tabelas, linhas e ações.

    timeout=None para não “morrer” enquanto o admin trabalha.
    """

    def __init__(
        self,
        *,
        nome_da_tabela: str | None = None,
        pagina: int = 0,
        linha_selecionada_pk: str | None = None,
        modo: str = "tabelas",
        mensagem_status: str | None = None,
    ):
        super().__init__(timeout=None)
        self.nome_da_tabela = nome_da_tabela
        self.pagina = max(0, pagina)
        self.linha_selecionada_pk = linha_selecionada_pk
        self.modo = modo
        self.mensagem_status = mensagem_status
        # preenchidos de forma assíncrona antes do envio quando possível
        self._total_linhas = 0
        self._linhas: list[dict] = []

    @classmethod
    async def criar(
        cls,
        *,
        nome_da_tabela: str | None = None,
        pagina: int = 0,
        linha_selecionada_pk: str | None = None,
        modo: str = "tabelas",
        mensagem_status: str | None = None,
    ) -> PainelBancoView:
        """
        Monta a view já com dados do banco carregados.
        """
        view = cls(
            nome_da_tabela=nome_da_tabela,
            pagina=pagina,
            linha_selecionada_pk=linha_selecionada_pk,
            modo=modo,
            mensagem_status=mensagem_status,
        )
        if nome_da_tabela and tabela_e_conhecida(nome_da_tabela):
            view._total_linhas = await contar_linhas(nome_da_tabela)
            view._linhas = await listar_linhas(
                nome_da_tabela,
                pagina=pagina,
            )
            if modo == "linha" and linha_selecionada_pk:
                view._linha_detalhe = await buscar_linha_por_pk(
                    nome_da_tabela,
                    decodificar_pk(linha_selecionada_pk),
                )
        view._montar()
        return view

    def _montar(self) -> None:
        for item in list(self.children):
            self.remove_item(item)

        if self.modo == "tabelas" or not self.nome_da_tabela:
            self._montar_lista_tabelas()
        elif self.modo == "linha" and self.linha_selecionada_pk:
            self._montar_detalhe_linha()
        else:
            self._montar_lista_linhas()

    def _montar_lista_tabelas(self) -> None:
        tabelas = listar_nomes_das_tabelas()
        texto = (
            "# Painel do banco de dados\n"
            "Escolha uma **tabela** para listar, editar ou apagar linhas.\n"
            "-# Somente administradores · alterações são imediatas no PostgreSQL"
        )
        if self.mensagem_status:
            texto += f"\n\n> {self.mensagem_status}"

        componentes: list = [discord.ui.TextDisplay(texto)]
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        # Discord limita 25 opções por select — fatia em blocos
        for indice_bloco in range(0, len(tabelas), 25):
            bloco = tabelas[indice_bloco : indice_bloco + 25]
            opcoes = [
                discord.SelectOption(
                    label=nome[:100],
                    value=nome,
                    description=f"Tabela `{nome}`"[:100],
                )
                for nome in bloco
            ]
            seletor = discord.ui.Select(
                placeholder=f"Tabelas ({indice_bloco + 1}–{indice_bloco + len(bloco)})…",
                options=opcoes,
                custom_id=f"{CUSTOM_SELECT_TABELA}:{indice_bloco}",
                min_values=1,
                max_values=1,
            )
            seletor.callback = self._ao_escolher_tabela
            linha = discord.ui.ActionRow()
            linha.add_item(seletor)
            componentes.append(linha)

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.dark_teal(),
            )
        )

    def _montar_lista_linhas(self) -> None:
        nome = self.nome_da_tabela or "?"
        total = self._total_linhas
        total_paginas = max(1, (total + LINHAS_POR_PAGINA - 1) // LINHAS_POR_PAGINA)
        pagina_humana = self.pagina + 1

        texto = (
            f"# Tabela `{nome}`\n"
            f"**{total}** linha(s) · página **{pagina_humana}/{total_paginas}**\n"
            "Selecione uma linha para ver detalhes, editar ou apagar."
        )
        if self.mensagem_status:
            texto += f"\n\n> {self.mensagem_status}"

        componentes: list = [discord.ui.TextDisplay(texto)]
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))

        if self._linhas:
            opcoes = []
            for linha in self._linhas:
                pk = pk_da_linha(nome, linha)
                opcoes.append(
                    discord.SelectOption(
                        label=resumo_da_linha(nome, linha)[:100],
                        value=codificar_pk(pk)[:100],
                    )
                )
            seletor = discord.ui.Select(
                placeholder="Escolher linha…",
                options=opcoes,
                custom_id=CUSTOM_SELECT_LINHA,
                min_values=1,
                max_values=1,
            )
            seletor.callback = self._ao_escolher_linha
            linha_sel = discord.ui.ActionRow()
            linha_sel.add_item(seletor)
            componentes.append(linha_sel)
        else:
            componentes.append(discord.ui.TextDisplay("-# Nenhuma linha nesta página."))

        # navegação
        linha_nav = discord.ui.ActionRow()
        botao_ant = discord.ui.Button(
            label="Anterior",
            style=discord.ButtonStyle.secondary,
            custom_id=CUSTOM_BTN_ANTERIOR,
            disabled=self.pagina <= 0,
        )
        botao_ant.callback = self._ao_pagina_anterior
        botao_prox = discord.ui.Button(
            label="Próxima",
            style=discord.ButtonStyle.secondary,
            custom_id=CUSTOM_BTN_PROXIMA,
            disabled=pagina_humana >= total_paginas,
        )
        botao_prox.callback = self._ao_pagina_proxima
        botao_ins = discord.ui.Button(
            label="Inserir linha",
            style=discord.ButtonStyle.success,
            custom_id=CUSTOM_BTN_INSERIR,
        )
        botao_ins.callback = self._ao_inserir
        botao_vol = discord.ui.Button(
            label="Tabelas",
            style=discord.ButtonStyle.primary,
            custom_id=CUSTOM_BTN_VOLTAR,
        )
        botao_vol.callback = self._ao_voltar_tabelas
        linha_nav.add_item(botao_ant)
        linha_nav.add_item(botao_prox)
        linha_nav.add_item(botao_ins)
        linha_nav.add_item(botao_vol)
        componentes.append(linha_nav)

        botao_at = discord.ui.Button(
            label="Atualizar",
            style=discord.ButtonStyle.secondary,
            custom_id=CUSTOM_BTN_ATUALIZAR,
        )
        botao_at.callback = self._ao_atualizar
        linha_at = discord.ui.ActionRow()
        linha_at.add_item(botao_at)
        componentes.append(linha_at)

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.blurple(),
            )
        )

    def _montar_detalhe_linha(self) -> None:
        nome = self.nome_da_tabela or "?"
        linha = getattr(self, "_linha_detalhe", None) or {}
        detalhes = ""
        if linha:
            partes = []
            for chave, valor in linha.items():
                texto_valor = "∅" if valor is None else str(valor)
                if len(texto_valor) > 80:
                    texto_valor = texto_valor[:77] + "..."
                partes.append(f"`{chave}`: {texto_valor}")
            detalhes = "\n".join(partes)
        componentes: list = [
            discord.ui.TextDisplay(
                f"# Linha em `{nome}`\n"
                f"PK: `{self.linha_selecionada_pk}`\n\n"
                f"{detalhes or '_Sem dados._'}\n\n"
                "Escolha um campo para editar, ou apague a linha."
            )
        ]
        if self.mensagem_status:
            componentes.append(discord.ui.TextDisplay(f"> {self.mensagem_status}"))
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        colunas = listar_colunas(nome)
        # select de campos (até 25)
        opcoes = [
            discord.SelectOption(label=coluna[:100], value=coluna)
            for coluna in colunas[:25]
            if coluna not in listar_chaves_primarias(nome)
        ]
        if opcoes:
            seletor = discord.ui.Select(
                placeholder="Campo para editar…",
                options=opcoes,
                custom_id=CUSTOM_SELECT_CAMPO,
            )
            seletor.callback = self._ao_escolher_campo
            linha_c = discord.ui.ActionRow()
            linha_c.add_item(seletor)
            componentes.append(linha_c)

        linha_acoes = discord.ui.ActionRow()
        botao_apagar = discord.ui.Button(
            label="Apagar linha",
            style=discord.ButtonStyle.danger,
            custom_id=CUSTOM_BTN_APAGAR,
        )
        botao_apagar.callback = self._ao_apagar
        botao_voltar = discord.ui.Button(
            label="Voltar às linhas",
            style=discord.ButtonStyle.secondary,
            custom_id=CUSTOM_BTN_VOLTAR,
        )
        botao_voltar.callback = self._ao_voltar_linhas
        linha_acoes.add_item(botao_apagar)
        linha_acoes.add_item(botao_voltar)
        componentes.append(linha_acoes)

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.orange(),
            )
        )

    async def _republicar(
        self,
        interacao: discord.Interaction,
        **kwargs,
    ) -> None:
        nova = await PainelBancoView.criar(**kwargs)
        if interacao.response.is_done():
            await editar_mensagem_original(interacao, view=nova)
        else:
            await interacao.response.edit_message(view=nova)

    async def _ao_escolher_tabela(self, interacao: discord.Interaction):
        if not _membro_e_admin(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas administradores."],
            )
            return
        nome = (interacao.data or {}).get("values", [None])[0]
        if not nome or not tabela_e_conhecida(nome):
            await responder_erro(
                interacao,
                titulo="Tabela inválida",
                linhas=["Escolha uma tabela da lista."],
            )
            return
        await self._republicar(
            interacao,
            nome_da_tabela=nome,
            pagina=0,
            modo="linhas",
        )

    async def _ao_escolher_linha(self, interacao: discord.Interaction):
        if not _membro_e_admin(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas administradores."],
            )
            return
        pk_codificada = (interacao.data or {}).get("values", [None])[0]
        await self._republicar(
            interacao,
            nome_da_tabela=self.nome_da_tabela,
            pagina=self.pagina,
            linha_selecionada_pk=pk_codificada,
            modo="linha",
        )

    async def _ao_pagina_anterior(self, interacao: discord.Interaction):
        await self._republicar(
            interacao,
            nome_da_tabela=self.nome_da_tabela,
            pagina=max(0, self.pagina - 1),
            modo="linhas",
        )

    async def _ao_pagina_proxima(self, interacao: discord.Interaction):
        await self._republicar(
            interacao,
            nome_da_tabela=self.nome_da_tabela,
            pagina=self.pagina + 1,
            modo="linhas",
        )

    async def _ao_atualizar(self, interacao: discord.Interaction):
        await self._republicar(
            interacao,
            nome_da_tabela=self.nome_da_tabela,
            pagina=self.pagina,
            modo="linhas",
            mensagem_status="Lista atualizada.",
        )

    async def _ao_voltar_tabelas(self, interacao: discord.Interaction):
        await self._republicar(interacao, modo="tabelas")

    async def _ao_voltar_linhas(self, interacao: discord.Interaction):
        await self._republicar(
            interacao,
            nome_da_tabela=self.nome_da_tabela,
            pagina=self.pagina,
            modo="linhas",
        )

    async def _ao_apagar(self, interacao: discord.Interaction):
        if not _membro_e_admin(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas administradores."],
            )
            return
        if not self.nome_da_tabela or not self.linha_selecionada_pk:
            await responder_erro(
                interacao,
                titulo="Nada selecionado",
                linhas=["Escolha uma linha antes de apagar."],
            )
            return
        valores_pk = decodificar_pk(self.linha_selecionada_pk)
        try:
            apagou = await apagar_linha_por_pk(self.nome_da_tabela, valores_pk)
        except Exception as erro:
            registrador.exception("Falha ao apagar linha: %s", erro)
            await responder_erro(
                interacao,
                titulo="Falha ao apagar",
                linhas=[str(erro)[:200]],
            )
            return
        status = "Linha apagada." if apagou else "Linha não encontrada."
        await self._republicar(
            interacao,
            nome_da_tabela=self.nome_da_tabela,
            pagina=self.pagina,
            modo="linhas",
            mensagem_status=status,
        )

    async def _ao_escolher_campo(self, interacao: discord.Interaction):
        if not _membro_e_admin(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas administradores."],
            )
            return
        campo = (interacao.data or {}).get("values", [None])[0]
        if not campo or not self.nome_da_tabela or not self.linha_selecionada_pk:
            await responder_erro(
                interacao,
                titulo="Seleção inválida",
                linhas=["Escolha um campo válido."],
            )
            return
        valores_pk = decodificar_pk(self.linha_selecionada_pk)
        linha = await buscar_linha_por_pk(self.nome_da_tabela, valores_pk)
        valor_atual = "" if linha is None else linha.get(campo)
        await interacao.response.send_modal(
            ModalEditarCampo(
                nome_da_tabela=self.nome_da_tabela,
                pk_codificada=self.linha_selecionada_pk,
                nome_do_campo=campo,
                valor_atual=valor_atual,
                pagina=self.pagina,
            )
        )

    async def _ao_inserir(self, interacao: discord.Interaction):
        if not _membro_e_admin(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas administradores."],
            )
            return
        if not self.nome_da_tabela:
            await responder_erro(
                interacao,
                titulo="Sem tabela",
                linhas=["Escolha uma tabela primeiro."],
            )
            return
        await interacao.response.send_modal(
            ModalInserirLinha(
                nome_da_tabela=self.nome_da_tabela,
                pagina=self.pagina,
            )
        )


class ModalEditarCampo(LoggingModalMixin, discord.ui.Modal, title="Editar campo"):
    def __init__(
        self,
        *,
        nome_da_tabela: str,
        pk_codificada: str,
        nome_do_campo: str,
        valor_atual,
        pagina: int,
    ):
        super().__init__()
        self.nome_da_tabela = nome_da_tabela
        self.pk_codificada = pk_codificada
        self.nome_do_campo = nome_do_campo
        self.pagina = pagina
        self.campo = discord.ui.TextInput(
            label=f"Valor de {nome_do_campo}"[:45],
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000,
            default=str(valor_atual) if valor_atual is not None else "",
        )
        self.add_item(self.campo)

    async def on_submit(self, interacao: discord.Interaction):
        if not _membro_e_admin(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas administradores."],
            )
            return
        valores_pk = decodificar_pk(self.pk_codificada)
        try:
            await atualizar_campo(
                self.nome_da_tabela,
                valores_pk,
                self.nome_do_campo,
                self.campo.value,
            )
        except Exception as erro:
            await responder_erro(
                interacao,
                titulo="Falha ao editar",
                linhas=[str(erro)[:300]],
            )
            return
        view = await PainelBancoView.criar(
            nome_da_tabela=self.nome_da_tabela,
            pagina=self.pagina,
            linha_selecionada_pk=self.pk_codificada,
            modo="linha",
            mensagem_status=f"Campo `{self.nome_do_campo}` atualizado.",
        )
        await interacao.response.edit_message(view=view)


class ModalInserirLinha(LoggingModalMixin, discord.ui.Modal, title="Inserir linha"):
    """
    Discord só permite 5 campos no modal. Usa as primeiras colunas
    não-PK autoincrementáveis quando possível.
    """

    def __init__(self, *, nome_da_tabela: str, pagina: int):
        super().__init__()
        self.nome_da_tabela = nome_da_tabela
        self.pagina = pagina
        self.campos_usados: list[str] = []
        self.inputs: list[discord.ui.TextInput] = []

        pks = set(listar_chaves_primarias(nome_da_tabela))
        colunas = [
            coluna for coluna in listar_colunas(nome_da_tabela) if coluna not in pks
        ][:5]
        # se não houver colunas além da PK, inclui a PK
        if not colunas:
            colunas = list(listar_chaves_primarias(nome_da_tabela))[:5]

        for coluna in colunas:
            entrada = discord.ui.TextInput(
                label=coluna[:45],
                style=discord.TextStyle.short,
                required=False,
                max_length=200,
                placeholder="vazio = omitir · null = NULL",
            )
            self.campos_usados.append(coluna)
            self.inputs.append(entrada)
            self.add_item(entrada)

    async def on_submit(self, interacao: discord.Interaction):
        if not _membro_e_admin(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas administradores."],
            )
            return
        valores: dict = {}
        for nome_campo, entrada in zip(self.campos_usados, self.inputs):
            bruto = (entrada.value or "").strip()
            if bruto == "":
                continue
            valores[nome_campo] = bruto
        try:
            await inserir_linha(self.nome_da_tabela, valores)
        except Exception as erro:
            await responder_erro(
                interacao,
                titulo="Falha ao inserir",
                linhas=[str(erro)[:300]],
            )
            return
        view = await PainelBancoView.criar(
            nome_da_tabela=self.nome_da_tabela,
            pagina=self.pagina,
            modo="linhas",
            mensagem_status="Linha inserida.",
        )
        await interacao.response.edit_message(view=view)
