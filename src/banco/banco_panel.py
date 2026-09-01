"""
Painel ephemeral de administração do banco (Components V2).

Sem timeout: fica aberto até o administrador fechar ou a mensagem ephemeral
expirar no Discord. Navegação por selects e botões; edição/inserção via modal.
Busca e filtro por coluna (discord_id, id_fivem, etc.) direto no painel.

Visual alinhado aos outros fluxos ephemeral do projeto (título #, seções ###,
separators, botões com emoji, accent_color por contexto).
"""

from __future__ import annotations

import logging

import discord

from src.banco.banco_service import (
    LINHAS_POR_PAGINA,
    ErroDependenciasBanco,
    apagar_linha_em_cascata,
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
    resumir_dependencias,
    resumo_da_linha,
    tabela_e_conhecida,
    texto_do_filtro_ativo,
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
CUSTOM_BTN_APAGAR_CASCATA = "banco:apagar_cascata"
CUSTOM_BTN_VOLTAR = "banco:voltar"
CUSTOM_BTN_BUSCAR = "banco:buscar"
CUSTOM_BTN_LIMPAR_FILTRO = "banco:limpar_filtro"

COR_TABELAS = discord.Color.dark_teal()
COR_LINHAS = discord.Color.blurple()
COR_DETALHE = discord.Color.orange()


def _membro_e_admin(membro: discord.Member | discord.User) -> bool:
    if not isinstance(membro, discord.Member):
        return False
    return bool(membro.guild_permissions.administrator)


def _placeholder_curto(texto: str, limite: int = 100) -> str:
    """Garante o limite do Discord em placeholders (≤ 100)."""
    texto = (texto or "").strip()
    if len(texto) <= limite:
        return texto
    return texto[: limite - 1] + "…"


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
        filtros: dict[str, str] | None = None,
        busca_livre: str | None = None,
    ):
        super().__init__(timeout=None)
        self.nome_da_tabela = nome_da_tabela
        self.pagina = max(0, pagina)
        self.linha_selecionada_pk = linha_selecionada_pk
        self.modo = modo
        self.mensagem_status = mensagem_status
        self.filtros = dict(filtros) if filtros else {}
        self.busca_livre = (busca_livre or "").strip() or None
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
        filtros: dict[str, str] | None = None,
        busca_livre: str | None = None,
    ) -> PainelBancoView:
        """Monta a view já com dados do banco carregados."""
        view = cls(
            nome_da_tabela=nome_da_tabela,
            pagina=pagina,
            linha_selecionada_pk=linha_selecionada_pk,
            modo=modo,
            mensagem_status=mensagem_status,
            filtros=filtros,
            busca_livre=busca_livre,
        )
        if nome_da_tabela and tabela_e_conhecida(nome_da_tabela):
            view._total_linhas = await contar_linhas(
                nome_da_tabela,
                filtros=view.filtros or None,
                busca_livre=view.busca_livre,
            )
            view._linhas = await listar_linhas(
                nome_da_tabela,
                pagina=pagina,
                filtros=view.filtros or None,
                busca_livre=view.busca_livre,
            )
            if modo == "linha" and linha_selecionada_pk:
                view._linha_detalhe = await buscar_linha_por_pk(
                    nome_da_tabela,
                    decodificar_pk(linha_selecionada_pk),
                )
        view._montar()
        return view

    def _kwargs_estado(self, **overrides) -> dict:
        """Estado atual da view, com sobrescritas opcionais para republicar."""
        base = {
            "nome_da_tabela": self.nome_da_tabela,
            "pagina": self.pagina,
            "linha_selecionada_pk": self.linha_selecionada_pk,
            "modo": self.modo,
            "mensagem_status": self.mensagem_status,
            "filtros": self.filtros or None,
            "busca_livre": self.busca_livre,
        }
        base.update(overrides)
        return base

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
            "# 🗄️ Painel do banco\n"
            "Escolha uma **tabela** para listar, buscar, editar ou apagar linhas.\n\n"
            "-# Somente administradores · alterações são imediatas no PostgreSQL"
        )
        if self.mensagem_status:
            texto += f"\n\n> {self.mensagem_status}"

        componentes: list = [
            discord.ui.TextDisplay(texto),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(
                f"### 📋 Tabelas disponíveis\n"
                f"`{len(tabelas)}` tabela(s) registradas no bot."
            ),
        ]

        for indice_bloco in range(0, len(tabelas), 25):
            bloco = tabelas[indice_bloco : indice_bloco + 25]
            opcoes = [
                discord.SelectOption(
                    label=nome[:100],
                    value=nome,
                    description=_placeholder_curto(f"Tabela `{nome}`"),
                )
                for nome in bloco
            ]
            seletor = discord.ui.Select(
                placeholder=_placeholder_curto(
                    f"Tabelas ({indice_bloco + 1}–{indice_bloco + len(bloco)})…"
                ),
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
                accent_color=COR_TABELAS,
            )
        )

    def _montar_lista_linhas(self) -> None:
        nome = self.nome_da_tabela or "?"
        total = self._total_linhas
        total_paginas = max(1, (total + LINHAS_POR_PAGINA - 1) // LINHAS_POR_PAGINA)
        pagina_humana = self.pagina + 1
        filtro_txt = texto_do_filtro_ativo(self.filtros, self.busca_livre)

        texto = (
            f"# 🗄️ Tabela `{nome}`\n"
            f"**{total}** linha(s) · página **{pagina_humana}/{total_paginas}**\n\n"
            "Selecione uma linha para ver detalhes, editar ou apagar."
        )
        if filtro_txt:
            texto += f"\n\n🔍 **Filtro ativo:** {filtro_txt}"
        if self.mensagem_status:
            texto += f"\n\n> {self.mensagem_status}"

        componentes: list = [
            discord.ui.TextDisplay(texto),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
        ]

        if self._linhas:
            componentes.append(discord.ui.TextDisplay("### 📄 Linhas desta página"))
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
                placeholder=_placeholder_curto("Escolher linha…"),
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
            componentes.append(
                discord.ui.TextDisplay(
                    "### 📄 Linhas desta página\n"
                    "-# Nenhuma linha" + (" com o filtro atual." if filtro_txt else ".")
                )
            )

        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        componentes.append(
            discord.ui.TextDisplay(
                "### 🔍 Busca e navegação\n"
                "-# Busque por `discord_id`, `id_fivem` ou qualquer coluna."
            )
        )

        linha_busca = discord.ui.ActionRow()
        botao_buscar = discord.ui.Button(
            label="Buscar",
            style=discord.ButtonStyle.primary,
            emoji="🔍",
            custom_id=CUSTOM_BTN_BUSCAR,
        )
        botao_buscar.callback = self._ao_buscar
        linha_busca.add_item(botao_buscar)

        tem_filtro = bool(filtro_txt)
        botao_limpar = discord.ui.Button(
            label="Limpar filtro",
            style=discord.ButtonStyle.secondary,
            emoji="🧹",
            custom_id=CUSTOM_BTN_LIMPAR_FILTRO,
            disabled=not tem_filtro,
        )
        botao_limpar.callback = self._ao_limpar_filtro
        linha_busca.add_item(botao_limpar)

        botao_at = discord.ui.Button(
            label="Atualizar",
            style=discord.ButtonStyle.secondary,
            emoji="🔄",
            custom_id=CUSTOM_BTN_ATUALIZAR,
        )
        botao_at.callback = self._ao_atualizar
        linha_busca.add_item(botao_at)
        componentes.append(linha_busca)

        linha_nav = discord.ui.ActionRow()
        botao_ant = discord.ui.Button(
            label="Anterior",
            style=discord.ButtonStyle.secondary,
            emoji="◀️",
            custom_id=CUSTOM_BTN_ANTERIOR,
            disabled=self.pagina <= 0,
        )
        botao_ant.callback = self._ao_pagina_anterior
        botao_prox = discord.ui.Button(
            label="Próxima",
            style=discord.ButtonStyle.secondary,
            emoji="▶️",
            custom_id=CUSTOM_BTN_PROXIMA,
            disabled=pagina_humana >= total_paginas,
        )
        botao_prox.callback = self._ao_pagina_proxima
        botao_ins = discord.ui.Button(
            label="Inserir",
            style=discord.ButtonStyle.success,
            emoji="➕",
            custom_id=CUSTOM_BTN_INSERIR,
        )
        botao_ins.callback = self._ao_inserir
        botao_vol = discord.ui.Button(
            label="Tabelas",
            style=discord.ButtonStyle.primary,
            emoji="📋",
            custom_id=CUSTOM_BTN_VOLTAR,
        )
        botao_vol.callback = self._ao_voltar_tabelas
        linha_nav.add_item(botao_ant)
        linha_nav.add_item(botao_prox)
        linha_nav.add_item(botao_ins)
        linha_nav.add_item(botao_vol)
        componentes.append(linha_nav)

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=COR_LINHAS,
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
                partes.append(f"> - **{chave}:** `{texto_valor}`")
            detalhes = "\n".join(partes)

        texto = (
            f"# 🗄️ Linha em `{nome}`\n"
            f"**PK:** `{self.linha_selecionada_pk}`\n\n"
            f"### 📌 Campos\n"
            f"{detalhes or '_Sem dados._'}"
        )
        if self.mensagem_status:
            texto += f"\n\n> {self.mensagem_status}"

        componentes: list = [
            discord.ui.TextDisplay(texto),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(
                "### ✏️ Ações\n"
                "-# Edite um campo, apague só esta linha, ou use **cascata** "
                "para remover também o que aponta para ela (FK)."
            ),
        ]

        colunas = listar_colunas(nome)
        pks = set(listar_chaves_primarias(nome))
        opcoes = [
            discord.SelectOption(label=coluna[:100], value=coluna)
            for coluna in colunas[:25]
            if coluna not in pks
        ]
        if opcoes:
            seletor = discord.ui.Select(
                placeholder=_placeholder_curto("Campo para editar…"),
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
            emoji="🗑️",
            custom_id=CUSTOM_BTN_APAGAR,
        )
        botao_apagar.callback = self._ao_apagar
        botao_cascata = discord.ui.Button(
            label="Apagar em cascata",
            style=discord.ButtonStyle.danger,
            emoji="💥",
            custom_id=CUSTOM_BTN_APAGAR_CASCATA,
        )
        botao_cascata.callback = self._ao_apagar_cascata
        botao_voltar = discord.ui.Button(
            label="Voltar às linhas",
            style=discord.ButtonStyle.secondary,
            emoji="◀️",
            custom_id=CUSTOM_BTN_VOLTAR,
        )
        botao_voltar.callback = self._ao_voltar_linhas
        linha_acoes.add_item(botao_apagar)
        linha_acoes.add_item(botao_cascata)
        linha_acoes.add_item(botao_voltar)
        componentes.append(linha_acoes)

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=COR_DETALHE,
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
            filtros=None,
            busca_livre=None,
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
            **self._kwargs_estado(
                linha_selecionada_pk=pk_codificada,
                modo="linha",
                mensagem_status=None,
            ),
        )

    async def _ao_pagina_anterior(self, interacao: discord.Interaction):
        await self._republicar(
            interacao,
            **self._kwargs_estado(
                pagina=max(0, self.pagina - 1),
                modo="linhas",
                linha_selecionada_pk=None,
                mensagem_status=None,
            ),
        )

    async def _ao_pagina_proxima(self, interacao: discord.Interaction):
        await self._republicar(
            interacao,
            **self._kwargs_estado(
                pagina=self.pagina + 1,
                modo="linhas",
                linha_selecionada_pk=None,
                mensagem_status=None,
            ),
        )

    async def _ao_atualizar(self, interacao: discord.Interaction):
        await self._republicar(
            interacao,
            **self._kwargs_estado(
                modo="linhas",
                linha_selecionada_pk=None,
                mensagem_status="Lista atualizada.",
            ),
        )

    async def _ao_voltar_tabelas(self, interacao: discord.Interaction):
        await self._republicar(
            interacao,
            modo="tabelas",
            filtros=None,
            busca_livre=None,
        )

    async def _ao_voltar_linhas(self, interacao: discord.Interaction):
        await self._republicar(
            interacao,
            **self._kwargs_estado(
                modo="linhas",
                linha_selecionada_pk=None,
                mensagem_status=None,
            ),
        )

    async def _ao_buscar(self, interacao: discord.Interaction):
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
            ModalBuscarLinha(
                nome_da_tabela=self.nome_da_tabela,
                filtros_atuais=self.filtros,
                busca_livre_atual=self.busca_livre,
            )
        )

    async def _ao_limpar_filtro(self, interacao: discord.Interaction):
        if not _membro_e_admin(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas administradores."],
            )
            return
        await self._republicar(
            interacao,
            nome_da_tabela=self.nome_da_tabela,
            pagina=0,
            modo="linhas",
            filtros=None,
            busca_livre=None,
            mensagem_status="Filtro removido.",
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
        except ErroDependenciasBanco as erro:
            registrador.warning("Apagar bloqueado por FK: %s", erro.mensagem)
            await responder_erro(
                interacao,
                titulo="Não foi possível apagar",
                linhas=[
                    erro.mensagem[:350],
                    "Use o botão **Apagar em cascata** nesta linha se quiser "
                    "remover também as dependências.",
                ],
            )
            return
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
            **self._kwargs_estado(
                pagina=self.pagina,
                modo="linhas",
                linha_selecionada_pk=None,
                mensagem_status=status,
            ),
        )

    async def _ao_apagar_cascata(self, interacao: discord.Interaction):
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
            dependencias = await resumir_dependencias(
                self.nome_da_tabela,
                valores_pk,
            )
            contagem = await apagar_linha_em_cascata(
                self.nome_da_tabela,
                valores_pk,
            )
        except ErroDependenciasBanco as erro:
            registrador.warning("Cascata bloqueada: %s", erro.mensagem)
            await responder_erro(
                interacao,
                titulo="Cascata incompleta",
                linhas=[erro.mensagem[:350]],
            )
            return
        except Exception as erro:
            registrador.exception("Falha ao apagar em cascata: %s", erro)
            await responder_erro(
                interacao,
                titulo="Falha ao apagar em cascata",
                linhas=[str(erro)[:200]],
            )
            return

        partes = [f"`{tabela}`: {qtd}" for tabela, qtd in sorted(contagem.items())]
        if not partes:
            status = "Nada foi apagado (linha já não existia)."
        else:
            extra = ""
            if dependencias:
                extra = f" Dependências prévias: {'; '.join(dependencias)}."
            status = "Cascata OK · " + "; ".join(partes) + "." + extra

        await self._republicar(
            interacao,
            **self._kwargs_estado(
                pagina=self.pagina,
                modo="linhas",
                linha_selecionada_pk=None,
                mensagem_status=status[:300],
            ),
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
                filtros=self.filtros,
                busca_livre=self.busca_livre,
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
                filtros=self.filtros,
                busca_livre=self.busca_livre,
            )
        )


class ModalBuscarLinha(LoggingModalMixin, discord.ui.Modal, title="🔍 Buscar no banco"):
    """
    Busca por coluna específica e/ou termo livre (discord_id, id_fivem, etc.).
    """

    def __init__(
        self,
        *,
        nome_da_tabela: str,
        filtros_atuais: dict[str, str] | None = None,
        busca_livre_atual: str | None = None,
    ):
        super().__init__()
        self.nome_da_tabela = nome_da_tabela
        self.filtros_atuais = dict(filtros_atuais) if filtros_atuais else {}

        self.coluna = discord.ui.TextInput(
            label="Coluna (opcional)",
            style=discord.TextStyle.short,
            required=False,
            max_length=60,
            placeholder=_placeholder_curto("ex: discord_id, id_fivem, status"),
            default="",
        )
        self.valor_coluna = discord.ui.TextInput(
            label="Valor da coluna",
            style=discord.TextStyle.short,
            required=False,
            max_length=100,
            placeholder=_placeholder_curto("igualdade exata · vazio = ignorar"),
            default="",
        )
        self.busca_livre = discord.ui.TextInput(
            label="Busca livre (IDs / texto)",
            style=discord.TextStyle.short,
            required=False,
            max_length=100,
            placeholder=_placeholder_curto("cole discord_id ou id_fivem"),
            default=(busca_livre_atual or "")[:100],
        )
        self.add_item(self.coluna)
        self.add_item(self.valor_coluna)
        self.add_item(self.busca_livre)

    async def on_submit(self, interacao: discord.Interaction):
        if not _membro_e_admin(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas administradores."],
            )
            return

        filtros: dict[str, str] = {}
        nome_coluna = (self.coluna.value or "").strip()
        valor = (self.valor_coluna.value or "").strip()
        livre = (self.busca_livre.value or "").strip() or None

        if nome_coluna:
            colunas_validas = set(listar_colunas(self.nome_da_tabela))
            if nome_coluna not in colunas_validas:
                await responder_erro(
                    interacao,
                    titulo="Coluna inválida",
                    linhas=[
                        f"`{nome_coluna}` não existe em `{self.nome_da_tabela}`.",
                        "Colunas: " + ", ".join(sorted(colunas_validas))[:300],
                    ],
                )
                return
            if valor:
                filtros[nome_coluna] = valor

        if not filtros and not livre:
            await responder_erro(
                interacao,
                titulo="Busca vazia",
                linhas=[
                    "Informe uma coluna + valor, ou uma busca livre "
                    "(discord_id, id_fivem, etc.).",
                ],
            )
            return

        try:
            total = await contar_linhas(
                self.nome_da_tabela,
                filtros=filtros or None,
                busca_livre=livre,
            )
        except Exception as erro:
            await responder_erro(
                interacao,
                titulo="Filtro inválido",
                linhas=[str(erro)[:300]],
            )
            return

        view = await PainelBancoView.criar(
            nome_da_tabela=self.nome_da_tabela,
            pagina=0,
            modo="linhas",
            filtros=filtros or None,
            busca_livre=livre,
            mensagem_status=f"Busca aplicada · {total} resultado(s).",
        )
        await interacao.response.edit_message(view=view)


class ModalEditarCampo(LoggingModalMixin, discord.ui.Modal, title="✏️ Editar campo"):
    def __init__(
        self,
        *,
        nome_da_tabela: str,
        pk_codificada: str,
        nome_do_campo: str,
        valor_atual,
        pagina: int,
        filtros: dict[str, str] | None = None,
        busca_livre: str | None = None,
    ):
        super().__init__()
        self.nome_da_tabela = nome_da_tabela
        self.pk_codificada = pk_codificada
        self.nome_do_campo = nome_do_campo
        self.pagina = pagina
        self.filtros = filtros
        self.busca_livre = busca_livre
        self.campo = discord.ui.TextInput(
            label=_placeholder_curto(f"Valor de {nome_do_campo}", 45),
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000,
            default=str(valor_atual) if valor_atual is not None else "",
            placeholder=_placeholder_curto("novo valor · null = NULL"),
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
            filtros=self.filtros,
            busca_livre=self.busca_livre,
            mensagem_status=f"Campo `{self.nome_do_campo}` atualizado.",
        )
        await interacao.response.edit_message(view=view)


class ModalInserirLinha(LoggingModalMixin, discord.ui.Modal, title="➕ Inserir linha"):
    """
    Discord só permite 5 campos no modal. Usa as primeiras colunas
    não-PK quando possível.
    """

    def __init__(
        self,
        *,
        nome_da_tabela: str,
        pagina: int,
        filtros: dict[str, str] | None = None,
        busca_livre: str | None = None,
    ):
        super().__init__()
        self.nome_da_tabela = nome_da_tabela
        self.pagina = pagina
        self.filtros = filtros
        self.busca_livre = busca_livre
        self.campos_usados: list[str] = []
        self.inputs: list[discord.ui.TextInput] = []

        pks = set(listar_chaves_primarias(nome_da_tabela))
        colunas = [
            coluna for coluna in listar_colunas(nome_da_tabela) if coluna not in pks
        ][:5]
        if not colunas:
            colunas = list(listar_chaves_primarias(nome_da_tabela))[:5]

        for coluna in colunas:
            entrada = discord.ui.TextInput(
                label=coluna[:45],
                style=discord.TextStyle.short,
                required=False,
                max_length=200,
                placeholder=_placeholder_curto("vazio = omitir · null = NULL"),
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
            filtros=self.filtros,
            busca_livre=self.busca_livre,
            mensagem_status="Linha inserida.",
        )
        await interacao.response.edit_message(view=view)
