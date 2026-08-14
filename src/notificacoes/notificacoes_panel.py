"""Painel persistente e fluxos ephemeral de notificação por DM.

Fluxo:
1. Painel fixo no canal CANAL_ENVIAR_NOTIFICACAO
2. Botão → ephemeral: escolher membro único OU cargo
3. Continuar → ephemeral editada: montar título / corpo / cor (com Voltar)
4. Enviar → DMs + log em LOG_NOTIFICACOES_DM
"""

from __future__ import annotations

import discord

from src.notificacoes.notificacoes_service import (
    LIMITE_NOTIFICACOES_POR_HORA,
    NOMES_CORES,
    ainda_pode_enviar,
    cor_da_sessao,
    destino_esta_pronto,
    enviar_notificacao_da_sessao,
    limpar_sessao,
    mensagem_esta_pronta,
    obter_sessao,
    quantidade_envios_na_hora,
    resumo_corpo,
    resumo_destino,
)
from src.plantao.permissoes import (
    e_diretoria,
    mensagem_sem_permissao,
)
from src.utils.error_handling import (
    LoggingModalMixin,
    LoggingViewMixin,
)
from src.utils.mensagens import (
    COR_ERRO,
    COR_INFO,
    COR_SUCESSO,
    responder_view,
)

# ═══════════════════════════════════════════════════════════════════════════
# PAINEL PRINCIPAL (persistente no canal)
# ═══════════════════════════════════════════════════════════════════════════


class PainelNotificacaoLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Card fixo do canal de envio de notificação por DM."""

    def __init__(self, guilda: discord.Guild):
        super().__init__(timeout=None)
        self.guilda = guilda

        url_icone = guilda.icon.url if guilda.icon else None

        if url_icone:
            bloco_cabecalho = discord.ui.Section(
                "# 📬 Painel de Notificação por DM\n\n"
                "> Use as opções abaixo para enviar notificações via mensagem "
                "direta aos membros do servidor!\n"
                "> Todas as notificações são registradas em log, portanto "
                "evite abusar!\n"
                "> Caso tenha dúvidas entre em contato com os Gerais!",
                accessory=discord.ui.Thumbnail(url_icone),
            )
        else:
            bloco_cabecalho = discord.ui.TextDisplay(
                "# 📬 Painel de Notificação por DM\n\n"
                "> Use as opções abaixo para enviar notificações via mensagem "
                "direta aos membros do servidor!\n"
                "> Todas as notificações são registradas em log, portanto "
                "evite abusar!\n"
                "> Caso tenha dúvidas entre em contato com os Gerais!"
            )

        linha_botao = discord.ui.ActionRow()
        botao_iniciar = discord.ui.Button(
            label="Iniciar notificação",
            style=discord.ButtonStyle.primary,
            emoji="📰",
            custom_id="notificacao:iniciar",
        )
        botao_iniciar.callback = self._ao_clicar_iniciar
        linha_botao.add_item(botao_iniciar)

        self.add_item(
            discord.ui.Container(
                bloco_cabecalho,
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                discord.ui.TextDisplay(
                    "## 📌 Antes de iniciar\n\n"
                    "✅ Certifique-se de que o membro **aceita receber DMs** "
                    "do servidor.\n"
                    "✅ Tenha em mãos o **nome, cargo ou ID** do destinatário.\n"
                    "✅ Verifique se o conteúdo da mensagem está de acordo com "
                    "as **regras do servidor**.\n"
                    "✅ Confirme se a notificação é realmente necessária para "
                    "evitar spam."
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(
                    "## 📌 Configurações Ativas\n\n"
                    "**➜ Sistema de Notificação por DM:** `✅ Configurado`\n"
                    "**➜ Registro de Logs:** `✅ Ativado`\n"
                    f"**➜ Limite de Notificações por Hora:** "
                    f"`{LIMITE_NOTIFICACOES_POR_HORA} por usuário`"
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha_botao,
                accent_color=discord.Color.blurple(),
            )
        )

    async def _ao_clicar_iniciar(self, interacao: discord.Interaction):
        membro = interacao.user
        if not isinstance(membro, discord.Member) or not e_diretoria(membro):
            await interacao.response.send_message(
                mensagem_sem_permissao("usar o painel de notificação"),
                ephemeral=True,
            )
            return

        if not ainda_pode_enviar(membro.id):
            usados = quantidade_envios_na_hora(membro.id)
            await interacao.response.send_message(
                f"❌ Limite de **{LIMITE_NOTIFICACOES_POR_HORA}** notificações "
                f"por hora atingido (`{usados}` neste período).\n"
                "Aguarde um pouco antes de enviar outra.",
                ephemeral=True,
            )
            return

        limpar_sessao(membro.id)
        obter_sessao(membro.id)
        await responder_view(
            interacao,
            FluxoEscolherDestinoView(id_do_executor=membro.id),
            ephemeral=True,
        )


# ═══════════════════════════════════════════════════════════════════════════
# ETAPA 1 — escolher destino (membro ou cargo)
# ═══════════════════════════════════════════════════════════════════════════


class FluxoEscolherDestinoView(LoggingViewMixin, discord.ui.LayoutView):
    """Ephemeral: tipo de destino + seleção + Continuar."""

    def __init__(self, id_do_executor: int):
        super().__init__(timeout=600)
        self.id_do_executor = id_do_executor
        self._reconstruir()

    def _reconstruir(self) -> None:
        self.clear_items()
        sessao = obter_sessao(self.id_do_executor)

        tipo_txt = {
            "membro": "👤 Membro único",
            "cargo": "🏷️ Cargo específico",
        }.get(sessao.tipo_destino or "", "*Nenhum*")

        # Select do tipo
        linha_tipo = discord.ui.ActionRow()
        seletor_tipo = discord.ui.Select(
            placeholder="Como deseja notificar?",
            options=[
                discord.SelectOption(
                    label="Membro único",
                    value="membro",
                    emoji="👤",
                    description="Um único membro (UserSelect ou ID Discord)",
                    default=sessao.tipo_destino == "membro",
                ),
                discord.SelectOption(
                    label="Cargo específico",
                    value="cargo",
                    emoji="🏷️",
                    description="Todos os membros com aquele cargo",
                    default=sessao.tipo_destino == "cargo",
                ),
            ],
        )
        seletor_tipo.callback = self._ao_escolher_tipo
        linha_tipo.add_item(seletor_tipo)

        componentes: list = [
            discord.ui.TextDisplay("# 📰 Iniciar notificação"),
            discord.ui.TextDisplay(
                "### Destino atual\n"
                f"> **Tipo:** {tipo_txt}\n"
                f"> **Alvo:** {resumo_destino(sessao)}\n\n"
                "Escolha o tipo, selecione o destino e clique em **Continuar**."
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay("### 1) Tipo de destino"),
            linha_tipo,
        ]

        if sessao.tipo_destino == "membro":
            linha_user = discord.ui.ActionRow()
            seletor_user = discord.ui.UserSelect(
                placeholder="Selecione o membro no Discord…",
                min_values=1,
                max_values=1,
            )
            seletor_user.callback = self._ao_escolher_membro
            linha_user.add_item(seletor_user)

            linha_id = discord.ui.ActionRow()
            botao_id = discord.ui.Button(
                label="Buscar membro pelo Discord ID",
                style=discord.ButtonStyle.secondary,
                emoji="🔍",
            )
            botao_id.callback = self._ao_buscar_id
            linha_id.add_item(botao_id)

            componentes.extend(
                [
                    discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                    discord.ui.TextDisplay("### 2) Escolher o membro"),
                    linha_user,
                    linha_id,
                ]
            )

        elif sessao.tipo_destino == "cargo":
            linha_cargo = discord.ui.ActionRow()
            seletor_cargo = discord.ui.RoleSelect(
                placeholder="Selecione o cargo…",
                min_values=1,
                max_values=1,
            )
            seletor_cargo.callback = self._ao_escolher_cargo
            linha_cargo.add_item(seletor_cargo)

            componentes.extend(
                [
                    discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                    discord.ui.TextDisplay(
                        "### 2) Escolher o cargo\n"
                        "-# Todos os membros **não-bot** com esse cargo "
                        "receberão a DM."
                    ),
                    linha_cargo,
                ]
            )

        linha_acoes = discord.ui.ActionRow()
        botao_continuar = discord.ui.Button(
            label="Continuar",
            style=discord.ButtonStyle.success,
            emoji="➡️",
            disabled=not destino_esta_pronto(sessao),
        )
        botao_continuar.callback = self._ao_continuar
        linha_acoes.add_item(botao_continuar)

        botao_cancelar = discord.ui.Button(
            label="Cancelar",
            style=discord.ButtonStyle.secondary,
            emoji="✖️",
        )
        botao_cancelar.callback = self._ao_cancelar
        linha_acoes.add_item(botao_cancelar)

        componentes.extend(
            [
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha_acoes,
            ]
        )

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.blurple(),
            )
        )

    async def _garantir_dono(self, interacao: discord.Interaction) -> bool:
        if interacao.user.id != self.id_do_executor:
            await interacao.response.send_message(
                "❌ Este painel não é seu.",
                ephemeral=True,
            )
            return False
        return True

    async def _atualizar(self, interacao: discord.Interaction) -> None:
        self._reconstruir()
        await interacao.response.edit_message(view=self)

    async def _ao_escolher_tipo(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        valor = interacao.data["values"][0]
        sessao = obter_sessao(self.id_do_executor)
        sessao.tipo_destino = valor
        # limpa destino anterior ao trocar o tipo
        sessao.id_do_membro = None
        sessao.mencao_do_membro = None
        sessao.id_do_cargo = None
        sessao.nome_do_cargo = None
        await self._atualizar(interacao)

    async def _ao_escolher_membro(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        id_membro = int(interacao.data["values"][0])
        membro = interacao.guild.get_member(id_membro) if interacao.guild else None
        if membro is None:
            await interacao.response.send_message(
                "❌ Membro não encontrado no servidor.",
                ephemeral=True,
            )
            return
        sessao = obter_sessao(self.id_do_executor)
        sessao.id_do_membro = membro.id
        sessao.mencao_do_membro = membro.mention
        await self._atualizar(interacao)

    async def _ao_buscar_id(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalDiscordIdNotificacao(self))

    async def _ao_escolher_cargo(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        id_cargo = int(interacao.data["values"][0])
        cargo = interacao.guild.get_role(id_cargo) if interacao.guild else None
        if cargo is None:
            await interacao.response.send_message(
                "❌ Cargo não encontrado.",
                ephemeral=True,
            )
            return
        sessao = obter_sessao(self.id_do_executor)
        sessao.id_do_cargo = cargo.id
        sessao.nome_do_cargo = cargo.name
        await self._atualizar(interacao)

    async def _ao_continuar(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        sessao = obter_sessao(self.id_do_executor)
        if not destino_esta_pronto(sessao):
            await interacao.response.send_message(
                "❌ Selecione o destino antes de continuar.",
                ephemeral=True,
            )
            return
        view_composicao = FluxoComporMensagemView(id_do_executor=self.id_do_executor)
        await interacao.response.edit_message(view=view_composicao)

    async def _ao_cancelar(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        limpar_sessao(self.id_do_executor)
        await interacao.response.edit_message(
            view=_view_mensagem_simples(
                titulo="❌ Notificação cancelada",
                linhas=["Nada foi enviado."],
                cor=COR_ERRO,
            )
        )


class ModalDiscordIdNotificacao(
    LoggingModalMixin, discord.ui.Modal, title="Buscar por Discord ID"
):
    campo_id = discord.ui.TextInput(
        label="ID do Discord do membro",
        placeholder="Ex.: 123456789012345678",
        required=True,
        min_length=15,
        max_length=22,
    )

    def __init__(self, painel: FluxoEscolherDestinoView):
        super().__init__()
        self.painel = painel

    async def on_submit(self, interacao: discord.Interaction):
        texto = self.campo_id.value.strip()
        if not texto.isdigit():
            await interacao.response.send_message(
                "❌ ID inválido. Use apenas números.",
                ephemeral=True,
            )
            return

        id_membro = int(texto)
        membro = interacao.guild.get_member(id_membro) if interacao.guild else None
        if membro is None and interacao.guild is not None:
            try:
                membro = await interacao.guild.fetch_member(id_membro)
            except (discord.NotFound, discord.HTTPException):
                membro = None

        if membro is None:
            await interacao.response.send_message(
                "❌ Membro não está no servidor (ou ID incorreto).",
                ephemeral=True,
            )
            return

        sessao = obter_sessao(self.painel.id_do_executor)
        sessao.tipo_destino = "membro"
        sessao.id_do_membro = membro.id
        sessao.mencao_do_membro = membro.mention
        sessao.id_do_cargo = None
        sessao.nome_do_cargo = None
        self.painel._reconstruir()
        await interacao.response.edit_message(view=self.painel)


# ═══════════════════════════════════════════════════════════════════════════
# ETAPA 2 — compor mensagem (estilo /templates, sem criador completo)
# ═══════════════════════════════════════════════════════════════════════════


class FluxoComporMensagemView(LoggingViewMixin, discord.ui.LayoutView):
    """Ephemeral editada: título, corpo, cor, preview, Voltar e Enviar."""

    def __init__(self, id_do_executor: int):
        super().__init__(timeout=600)
        self.id_do_executor = id_do_executor
        self._reconstruir()

    def _reconstruir(self) -> None:
        self.clear_items()
        sessao = obter_sessao(self.id_do_executor)

        titulo_txt = sessao.titulo.strip() if sessao.titulo.strip() else "*Pendente*"
        nome_cor = NOMES_CORES.get(sessao.chave_cor, sessao.chave_cor)
        corpo_txt = resumo_corpo(sessao)

        linha_edicao = discord.ui.ActionRow()
        botao_titulo = discord.ui.Button(
            label="Definir título",
            style=discord.ButtonStyle.primary,
            emoji="📌",
        )
        botao_titulo.callback = self._ao_definir_titulo
        linha_edicao.add_item(botao_titulo)

        botao_corpo = discord.ui.Button(
            label="Definir mensagem",
            style=discord.ButtonStyle.primary,
            emoji="✏️",
        )
        botao_corpo.callback = self._ao_definir_corpo
        linha_edicao.add_item(botao_corpo)

        linha_cor = discord.ui.ActionRow()
        seletor_cor = discord.ui.Select(
            placeholder="Cor do card na DM…",
            options=[
                discord.SelectOption(
                    label=nome,
                    value=chave,
                    default=sessao.chave_cor == chave,
                )
                for chave, nome in NOMES_CORES.items()
            ],
        )
        seletor_cor.callback = self._ao_escolher_cor
        linha_cor.add_item(seletor_cor)

        linha_acoes = discord.ui.ActionRow()
        botao_voltar = discord.ui.Button(
            label="Voltar",
            style=discord.ButtonStyle.secondary,
            emoji="⬅️",
        )
        botao_voltar.callback = self._ao_voltar
        linha_acoes.add_item(botao_voltar)

        botao_enviar = discord.ui.Button(
            label="Enviar notificação",
            style=discord.ButtonStyle.success,
            emoji="📨",
            disabled=not mensagem_esta_pronta(sessao),
        )
        botao_enviar.callback = self._ao_enviar
        linha_acoes.add_item(botao_enviar)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay("# ✏️ Montar a mensagem"),
                discord.ui.TextDisplay(
                    "### Destino\n"
                    f"> {resumo_destino(sessao)}\n\n"
                    "### Pré-visualização (ao vivo)\n"
                    f"> **Título:** {titulo_txt}\n"
                    f"> **Cor:** `{nome_cor}`\n"
                    f"> **Corpo:**\n```\n{corpo_txt}\n```\n"
                    "-# Use `|` no corpo para quebrar linhas no card da DM."
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                discord.ui.TextDisplay("### Editar conteúdo"),
                linha_edicao,
                linha_cor,
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha_acoes,
                accent_color=cor_da_sessao(sessao),
            )
        )

    async def _garantir_dono(self, interacao: discord.Interaction) -> bool:
        if interacao.user.id != self.id_do_executor:
            await interacao.response.send_message(
                "❌ Este painel não é seu.",
                ephemeral=True,
            )
            return False
        return True

    async def _atualizar(self, interacao: discord.Interaction) -> None:
        self._reconstruir()
        await interacao.response.edit_message(view=self)

    async def _ao_definir_titulo(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalTituloNotificacao(self))

    async def _ao_definir_corpo(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalCorpoNotificacao(self))

    async def _ao_escolher_cor(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        sessao = obter_sessao(self.id_do_executor)
        sessao.chave_cor = interacao.data["values"][0]
        await self._atualizar(interacao)

    async def _ao_voltar(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        # mantém destino e também o rascunho da mensagem
        view_destino = FluxoEscolherDestinoView(id_do_executor=self.id_do_executor)
        await interacao.response.edit_message(view=view_destino)

    async def _ao_enviar(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return

        sessao = obter_sessao(self.id_do_executor)
        if not destino_esta_pronto(sessao) or not mensagem_esta_pronta(sessao):
            await interacao.response.send_message(
                "❌ Complete o destino e a mensagem antes de enviar.",
                ephemeral=True,
            )
            return

        if not ainda_pode_enviar(self.id_do_executor):
            await interacao.response.send_message(
                f"❌ Limite de **{LIMITE_NOTIFICACOES_POR_HORA}** por hora atingido.",
                ephemeral=True,
            )
            return

        if interacao.guild is None:
            await interacao.response.send_message(
                "❌ Este fluxo só funciona dentro do servidor.",
                ephemeral=True,
            )
            return

        await interacao.response.defer(ephemeral=True)

        resultado = await enviar_notificacao_da_sessao(interacao.guild, sessao)

        if resultado.total == 0:
            titulo = "❌ Nenhum destinatário"
            linhas = [
                "Não foi possível resolver membros para o destino escolhido.",
                "Confira se o cargo tem membros online no cache ou se o ID está correto.",
            ]
            cor = COR_ERRO
        elif resultado.enviados == 0:
            titulo = "❌ Falha no envio"
            linhas = [
                f"Tentativas: **{resultado.total}**",
                "Nenhuma DM chegou (DMs fechadas ou bot sem permissão).",
            ]
            cor = COR_ERRO
        elif resultado.falhas == 0:
            titulo = "✅ Notificação enviada"
            linhas = [
                f"Destino: {resumo_destino(sessao)}",
                f"Enviadas: **{resultado.enviados}** / {resultado.total}",
                f"Título: **{sessao.titulo[:80]}**",
            ]
            cor = COR_SUCESSO
        else:
            titulo = "⚠️ Envio parcial"
            linhas = [
                f"Destino: {resumo_destino(sessao)}",
                f"Enviadas: **{resultado.enviados}** · Falhas: **{resultado.falhas}**",
                f"(total resolvido: {resultado.total})",
            ]
            cor = COR_INFO

        limpar_sessao(self.id_do_executor)
        await interacao.edit_original_response(
            view=_view_mensagem_simples(titulo=titulo, linhas=linhas, cor=cor)
        )


class ModalTituloNotificacao(
    LoggingModalMixin, discord.ui.Modal, title="Título da notificação"
):
    campo_titulo = discord.ui.TextInput(
        label="Título (aparece no topo do card)",
        placeholder="Ex.: Aviso importante da Diretoria",
        required=True,
        max_length=200,
        style=discord.TextStyle.short,
    )

    def __init__(self, painel: FluxoComporMensagemView):
        super().__init__()
        self.painel = painel
        sessao = obter_sessao(painel.id_do_executor)
        if sessao.titulo:
            self.campo_titulo.default = sessao.titulo[:200]

    async def on_submit(self, interacao: discord.Interaction):
        sessao = obter_sessao(self.painel.id_do_executor)
        sessao.titulo = self.campo_titulo.value.strip()
        self.painel._reconstruir()
        await interacao.response.edit_message(view=self.painel)


class ModalCorpoNotificacao(
    LoggingModalMixin, discord.ui.Modal, title="Corpo da mensagem"
):
    campo_corpo = discord.ui.TextInput(
        label="Mensagem (use | para quebrar linha)",
        placeholder="Linha 1 | Linha 2 | Linha 3",
        required=True,
        max_length=1500,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, painel: FluxoComporMensagemView):
        super().__init__()
        self.painel = painel
        sessao = obter_sessao(painel.id_do_executor)
        if sessao.linhas_corpo:
            self.campo_corpo.default = " | ".join(sessao.linhas_corpo)[:1500]

    async def on_submit(self, interacao: discord.Interaction):
        texto = self.campo_corpo.value.strip()
        # aceita tanto | quanto quebras de linha reais do modal
        partes: list[str] = []
        for pedaco in texto.replace("\r\n", "\n").split("\n"):
            for sub in pedaco.split("|"):
                limpo = sub.strip()
                if limpo:
                    partes.append(limpo)
        sessao = obter_sessao(self.painel.id_do_executor)
        sessao.linhas_corpo = partes or [texto]
        self.painel._reconstruir()
        await interacao.response.edit_message(view=self.painel)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers de UI
# ═══════════════════════════════════════════════════════════════════════════


def _view_mensagem_simples(
    *,
    titulo: str,
    linhas: list[str],
    cor: discord.Color,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=120)
    texto_linhas = "\n".join(f"> {linha}" for linha in linhas)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(f"# {titulo}"),
            discord.ui.TextDisplay(texto_linhas),
            accent_color=cor,
        )
    )
    return view
