"""Views efêmeras do fluxo de rebaixamento (estilo painel de ausência)."""

from __future__ import annotations

import logging

import discord

from src.database.conexao import async_session
from src.database.models import Usuario
from src.rebaixamento.rebaixamento_service import (
    DIAS_BLOQUEIO_OPCOES,
    aplicar_rebaixamento,
    cargo_um_degrau_abaixo,
    listar_cargos_inferiores,
    listar_rebaixamentos_ativos,
    membro_e_diretoria_geral,
    membro_pode_rebaixar,
    membro_pode_revogar_rebaixamento,
    obter_historico_do_rebaixamento,
    publicar_log_rebaixamento,
    publicar_log_revogacao_rebaixamento,
    revogar_rebaixamento,
    alvo_pode_ser_rebaixado,
)
from src.utils.error_handling import LoggingModalMixin, LoggingViewMixin
from src.plantao.plantao_permissoes import mensagem_sem_permissao
from src.utils.mensagens import (
    responder_erro,
    responder_sucesso,
    responder_view,
)
from sqlalchemy import select

registrador = logging.getLogger(__name__)


async def _resolver_id_fivem(discord_id: int) -> str | None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(Usuario).where(Usuario.discord_id == discord_id)
        )
        usuario = resultado.scalar_one_or_none()
        if usuario is None:
            return None
        return usuario.id_fivem


async def _contar_metas_baseline(discord_id: int) -> dict[str, int]:
    """Reaproveita a contagem de metas do domínio de promoção, se disponível."""
    try:
        from src.promocoes.promocoes_service import _contar_metas_do_membro

        return await _contar_metas_do_membro(discord_id)
    except Exception as erro:  # noqa: BLE001 — baseline opcional
        registrador.warning("Baseline de metas indisponível: %s", erro)
        return {}


def _checar_executor(interacao: discord.Interaction) -> discord.Member | None:
    if not isinstance(interacao.user, discord.Member):
        return None
    if not membro_pode_rebaixar(interacao.user):
        return None
    return interacao.user


# ---------------------------------------------------------------------------
# Entrada: buscar membro (UserSelect + Discord ID + FiveM)
# ---------------------------------------------------------------------------


class PainelBuscaRebaixarLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Painel efêmero inicial do /rebaixar."""

    def __init__(self) -> None:
        super().__init__(timeout=300)
        componentes: list = [
            discord.ui.TextDisplay(
                "# ⬇️ Rebaixar membro\n"
                "> Selecione o membro ou busque por Discord ID / FiveM.\n"
                "Acesso restrito a **Diretor++**."
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
        ]
        linha_user = discord.ui.ActionRow()
        seletor = discord.ui.UserSelect(
            placeholder="Selecione o membro…",
            min_values=1,
            max_values=1,
        )
        seletor.callback = self._ao_user_select
        linha_user.add_item(seletor)
        componentes.append(linha_user)

        linha_busca = discord.ui.ActionRow()
        b_discord = discord.ui.Button(
            label="Discord ID",
            style=discord.ButtonStyle.secondary,
            emoji="🔍",
        )
        b_discord.callback = self._ao_discord_id
        b_fivem = discord.ui.Button(
            label="FiveM",
            style=discord.ButtonStyle.secondary,
            emoji="🪪",
        )
        b_fivem.callback = self._ao_fivem
        linha_busca.add_item(b_discord)
        linha_busca.add_item(b_fivem)
        componentes.append(linha_busca)

        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        linha_revogar = discord.ui.ActionRow()
        b_revogar = discord.ui.Button(
            label="Revogar rebaixamento",
            style=discord.ButtonStyle.secondary,
            emoji="♻️",
        )
        b_revogar.callback = self._ao_abrir_revogar
        linha_revogar.add_item(b_revogar)
        componentes.append(linha_revogar)
        componentes.append(
            discord.ui.TextDisplay(
                "-# Revogar: apenas **EQUIPE・DIRETORIA GERAL**, "
                "**Responsavel HP** ou **Administrador**."
            )
        )

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.dark_red(),
            )
        )

    async def _ao_user_select(self, interacao: discord.Interaction) -> None:
        executor = _checar_executor(interacao)
        if executor is None:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("rebaixar membros")],
            )
            return
        valores = interacao.data.get("values") if interacao.data else None
        if not valores:
            await responder_erro(
                interacao, titulo="Seleção", linhas=["Nenhum membro selecionado."]
            )
            return
        membro_id = int(valores[0])
        membro = interacao.guild.get_member(membro_id) if interacao.guild else None
        if membro is None:
            await responder_erro(
                interacao,
                titulo="Fora do servidor",
                linhas=["O membro precisa estar no servidor para ser rebaixado."],
            )
            return
        await abrir_fluxo_rebaixamento(interacao, membro)

    async def _ao_discord_id(self, interacao: discord.Interaction) -> None:
        if _checar_executor(interacao) is None:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("rebaixar membros")],
            )
            return
        await interacao.response.send_modal(ModalBuscarDiscordRebaixar())

    async def _ao_fivem(self, interacao: discord.Interaction) -> None:
        if _checar_executor(interacao) is None:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("rebaixar membros")],
            )
            return
        await interacao.response.send_modal(ModalBuscarFivemRebaixar())

    async def _ao_abrir_revogar(self, interacao: discord.Interaction) -> None:
        if not isinstance(interacao.user, discord.Member):
            await responder_erro(
                interacao, titulo="Contexto", linhas=["Use no servidor."]
            )
            return
        if not membro_pode_revogar_rebaixamento(interacao.user):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[
                    mensagem_sem_permissao(
                        "revogar rebaixamento "
                        "(EQUIPE・DIRETORIA GERAL, Responsavel HP ou Administrador)"
                    )
                ],
            )
            return
        await responder_view(
            interacao,
            PainelBuscaRevogarRebaixamentoLayout(),
            ephemeral=True,
        )


class ModalBuscarDiscordRebaixar(LoggingModalMixin, discord.ui.Modal, title="Discord ID"):
    discord_id_input = discord.ui.TextInput(
        label="Discord ID", required=True, max_length=20
    )

    async def on_submit(self, interacao: discord.Interaction) -> None:
        if _checar_executor(interacao) is None:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("rebaixar membros")],
            )
            return
        raw = self.discord_id_input.value.strip()
        if not raw.isdigit():
            await responder_erro(
                interacao, titulo="ID inválido", linhas=["Somente números."]
            )
            return
        membro_id = int(raw)
        membro = interacao.guild.get_member(membro_id) if interacao.guild else None
        if membro is None:
            await responder_erro(
                interacao,
                titulo="Fora do servidor",
                linhas=[f"<@{membro_id}> precisa estar no servidor."],
            )
            return
        await abrir_fluxo_rebaixamento(interacao, membro)


class ModalBuscarFivemRebaixar(LoggingModalMixin, discord.ui.Modal, title="ID FiveM"):
    id_fivem_input = discord.ui.TextInput(
        label="ID FiveM", required=True, max_length=20
    )

    async def on_submit(self, interacao: discord.Interaction) -> None:
        if _checar_executor(interacao) is None:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("rebaixar membros")],
            )
            return
        valor = self.id_fivem_input.value.strip()
        if not valor.isdigit():
            await responder_erro(
                interacao, titulo="ID inválido", linhas=["Somente números."]
            )
            return
        async with async_session() as sessao:
            resultado = await sessao.execute(
                select(Usuario).where(Usuario.id_fivem == valor)
            )
            usuario = resultado.scalar_one_or_none()
        if usuario is None:
            await responder_erro(
                interacao,
                titulo="Não encontrado",
                linhas=[f"FiveM `{valor}` não cadastrado."],
            )
            return
        membro = (
            interacao.guild.get_member(usuario.discord_id) if interacao.guild else None
        )
        if membro is None:
            await responder_erro(
                interacao,
                titulo="Fora do servidor",
                linhas=[f"FiveM `{valor}` → <@{usuario.discord_id}> fora do servidor."],
            )
            return
        await abrir_fluxo_rebaixamento(interacao, membro)


# ---------------------------------------------------------------------------
# Fluxo: escolher destino → bloqueio → motivo → confirmar
# ---------------------------------------------------------------------------


async def abrir_fluxo_rebaixamento(
    interacao: discord.Interaction,
    alvo: discord.Member,
) -> None:
    """Abre o painel de escolha de destino para o alvo."""
    executor = _checar_executor(interacao)
    if executor is None:
        await responder_erro(
            interacao,
            titulo="Sem permissão",
            linhas=[mensagem_sem_permissao("rebaixar membros")],
        )
        return

    ok, detalhe = alvo_pode_ser_rebaixado(alvo, executor)
    if not ok:
        await responder_erro(interacao, titulo="Não rebaixável", linhas=[detalhe])
        return

    cargo_atual = detalhe
    degrau = cargo_um_degrau_abaixo(cargo_atual)
    pode_livre = membro_e_diretoria_geral(executor)
    fid = await _resolver_id_fivem(alvo.id)

    await responder_view(
        interacao,
        PainelDestinoRebaixamentoLayout(
            alvo=alvo,
            cargo_atual=cargo_atual,
            cargo_degrau=degrau,
            pode_livre=pode_livre,
            id_fivem=fid,
        ),
        ephemeral=True,
    )


class PainelDestinoRebaixamentoLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Escolha: um degrau ou (Diretoria Geral) cargo livre."""

    def __init__(
        self,
        *,
        alvo: discord.Member,
        cargo_atual: str,
        cargo_degrau: str | None,
        pode_livre: bool,
        id_fivem: str | None,
    ) -> None:
        super().__init__(timeout=300)
        self.alvo = alvo
        self.cargo_atual = cargo_atual
        self.cargo_degrau = cargo_degrau
        self.pode_livre = pode_livre

        texto = (
            f"# ⬇️ Rebaixar {alvo.display_name}\n"
            f"**Membro:** {alvo.mention} (`{alvo.id}`)\n"
            f"**FiveM:** `{id_fivem or '—'}`\n"
            f"**Cargo atual:** {cargo_atual}\n"
        )
        if cargo_degrau:
            texto += f"**Um degrau:** {cargo_degrau}\n"
        else:
            texto += "**Um degrau:** _(já no cargo mais baixo)_\n"
        if pode_livre:
            texto += "\nVocê tem **EQUIPE・DIRETORIA GERAL** — pode escolher qualquer cargo inferior."

        componentes: list = [
            discord.ui.TextDisplay(texto),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
        ]

        linha = discord.ui.ActionRow()
        if cargo_degrau:
            botao_degrau = discord.ui.Button(
                label="Um degrau",
                style=discord.ButtonStyle.danger,
                emoji="⬇️",
            )
            botao_degrau.callback = self._ao_um_degrau
            linha.add_item(botao_degrau)

        if pode_livre:
            inferiores = listar_cargos_inferiores(cargo_atual)
            if inferiores:
                opcoes = [
                    discord.SelectOption(
                        label=nome[:100],
                        value=nome,
                        description=f"Rebaixar para {nome[:50]}",
                    )
                    for nome in inferiores[:25]
                ]
                seletor = discord.ui.Select(
                    placeholder="Cargo livre (Diretoria Geral)…",
                    options=opcoes,
                    min_values=1,
                    max_values=1,
                )
                seletor.callback = self._ao_livre
                linha_select = discord.ui.ActionRow()
                linha_select.add_item(seletor)
                componentes.append(linha)
                componentes.append(linha_select)
            else:
                componentes.append(linha)
        else:
            componentes.append(linha)

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.dark_red(),
            )
        )

    async def _ao_um_degrau(self, interacao: discord.Interaction) -> None:
        if _checar_executor(interacao) is None:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("rebaixar membros")],
            )
            return
        if not self.cargo_degrau:
            await responder_erro(
                interacao,
                titulo="Sem degrau",
                linhas=["Não há cargo abaixo deste."],
            )
            return
        await _abrir_escolha_bloqueio(
            interacao,
            alvo=self.alvo,
            cargo_de=self.cargo_atual,
            cargo_para=self.cargo_degrau,
            modo="um_degrau",
        )

    async def _ao_livre(self, interacao: discord.Interaction) -> None:
        executor = _checar_executor(interacao)
        if executor is None or not membro_e_diretoria_geral(executor):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas EQUIPE・DIRETORIA GERAL pode usar cargo livre."],
            )
            return
        valores = interacao.data.get("values") if interacao.data else None
        if not valores:
            await responder_erro(
                interacao, titulo="Seleção", linhas=["Escolha um cargo."]
            )
            return
        cargo_para = valores[0]
        await _abrir_escolha_bloqueio(
            interacao,
            alvo=self.alvo,
            cargo_de=self.cargo_atual,
            cargo_para=cargo_para,
            modo="livre",
        )


async def _abrir_escolha_bloqueio(
    interacao: discord.Interaction,
    *,
    alvo: discord.Member,
    cargo_de: str,
    cargo_para: str,
    modo: str,
) -> None:
    await responder_view(
        interacao,
        PainelBloqueioRebaixamentoLayout(
            alvo=alvo,
            cargo_de=cargo_de,
            cargo_para=cargo_para,
            modo=modo,
        ),
        ephemeral=True,
    )


class PainelBloqueioRebaixamentoLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Escolha 7 / 15 / 21 dias de bloqueio de promoção."""

    def __init__(
        self,
        *,
        alvo: discord.Member,
        cargo_de: str,
        cargo_para: str,
        modo: str,
    ) -> None:
        super().__init__(timeout=300)
        self.alvo = alvo
        self.cargo_de = cargo_de
        self.cargo_para = cargo_para
        self.modo = modo

        texto = (
            f"# ⬇️ Bloqueio de promoção\n"
            f"**{alvo.mention}**: `{cargo_de}` → `{cargo_para}`\n"
            f"Escolha quantos dias o membro **não poderá solicitar promoção**."
        )
        componentes: list = [
            discord.ui.TextDisplay(texto),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
        ]
        linha = discord.ui.ActionRow()
        for dias in DIAS_BLOQUEIO_OPCOES:
            botao = discord.ui.Button(
                label=f"{dias} dias",
                style=discord.ButtonStyle.primary,
                custom_id=f"rebaix:dias:{dias}",
            )
            botao.callback = self._bind_dias(dias)
            linha.add_item(botao)
        componentes.append(linha)
        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.dark_red(),
            )
        )

    def _bind_dias(self, dias: int):
        async def _callback(interacao: discord.Interaction) -> None:
            if _checar_executor(interacao) is None:
                await responder_erro(
                    interacao,
                    titulo="Sem permissão",
                    linhas=[mensagem_sem_permissao("rebaixar membros")],
                )
                return
            await interacao.response.send_modal(
                ModalMotivoRebaixamento(
                    alvo=self.alvo,
                    cargo_de=self.cargo_de,
                    cargo_para=self.cargo_para,
                    modo=self.modo,
                    dias_bloqueio=dias,
                )
            )

        return _callback


class ModalMotivoRebaixamento(LoggingModalMixin, discord.ui.Modal, title="Motivo do rebaixamento"):
    motivo_input = discord.ui.TextInput(
        label="Motivo (obrigatório)",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500,
        placeholder="Descreva o motivo do rebaixamento…",
    )

    def __init__(
        self,
        *,
        alvo: discord.Member,
        cargo_de: str,
        cargo_para: str,
        modo: str,
        dias_bloqueio: int,
    ) -> None:
        super().__init__()
        self.alvo = alvo
        self.cargo_de = cargo_de
        self.cargo_para = cargo_para
        self.modo = modo
        self.dias_bloqueio = dias_bloqueio

    async def on_submit(self, interacao: discord.Interaction) -> None:
        executor = _checar_executor(interacao)
        if executor is None:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("rebaixar membros")],
            )
            return
        motivo = (self.motivo_input.value or "").strip()
        if not motivo:
            await responder_erro(
                interacao, titulo="Motivo", linhas=["O motivo é obrigatório."]
            )
            return

        await responder_view(
            interacao,
            PainelConfirmarRebaixamentoLayout(
                alvo=self.alvo,
                cargo_de=self.cargo_de,
                cargo_para=self.cargo_para,
                modo=self.modo,
                dias_bloqueio=self.dias_bloqueio,
                motivo=motivo,
            ),
            ephemeral=True,
        )


class PainelConfirmarRebaixamentoLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Confirmação final antes de aplicar."""

    def __init__(
        self,
        *,
        alvo: discord.Member,
        cargo_de: str,
        cargo_para: str,
        modo: str,
        dias_bloqueio: int,
        motivo: str,
    ) -> None:
        super().__init__(timeout=180)
        self.alvo = alvo
        self.cargo_de = cargo_de
        self.cargo_para = cargo_para
        self.modo = modo
        self.dias_bloqueio = dias_bloqueio
        self.motivo = motivo

        texto = (
            f"# ⚠️ Confirmar rebaixamento\n"
            f"**Membro:** {alvo.mention}\n"
            f"**De:** {cargo_de}\n"
            f"**Para:** {cargo_para}\n"
            f"**Modo:** {modo}\n"
            f"**Bloqueio:** {dias_bloqueio} dias\n"
            f"**Motivo:** {motivo}\n\n"
            f"Cargos de área (RESP.*) serão **removidos**. "
            f"Solicitação de promoção pendente será **cancelada**."
        )
        componentes: list = [
            discord.ui.TextDisplay(texto),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
        ]
        linha = discord.ui.ActionRow()
        botao_ok = discord.ui.Button(
            label="Confirmar rebaixamento",
            style=discord.ButtonStyle.danger,
            emoji="⬇️",
        )
        botao_ok.callback = self._ao_confirmar
        botao_no = discord.ui.Button(
            label="Cancelar",
            style=discord.ButtonStyle.secondary,
        )
        botao_no.callback = self._ao_cancelar
        linha.add_item(botao_ok)
        linha.add_item(botao_no)
        componentes.append(linha)
        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.dark_red(),
            )
        )

    async def _ao_cancelar(self, interacao: discord.Interaction) -> None:
        await responder_sucesso(
            interacao,
            titulo="Cancelado",
            linhas=["Rebaixamento não aplicado."],
        )

    async def _ao_confirmar(self, interacao: discord.Interaction) -> None:
        executor = _checar_executor(interacao)
        if executor is None or interacao.guild is None:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("rebaixar membros")],
            )
            return

        # Revalida alvo ainda no servidor com o cargo esperado
        alvo = interacao.guild.get_member(self.alvo.id)
        if alvo is None:
            await responder_erro(
                interacao,
                titulo="Alvo",
                linhas=["O membro saiu do servidor."],
            )
            return

        await interacao.response.defer(ephemeral=True)
        metas = await _contar_metas_baseline(alvo.id)
        ok, detalhe = await aplicar_rebaixamento(
            guild=interacao.guild,
            alvo=alvo,
            executor=executor,
            cargo_para=self.cargo_para,
            motivo=self.motivo,
            dias_bloqueio=self.dias_bloqueio,
            modo=self.modo,
            metas_atuais=metas,
        )
        if not ok:
            await responder_erro(interacao, titulo="Falha", linhas=[detalhe])
            return

        await publicar_log_rebaixamento(
            interacao.guild,
            alvo=alvo,
            executor=executor,
            cargo_de=self.cargo_de,
            cargo_para=self.cargo_para,
            motivo=self.motivo,
            dias_bloqueio=self.dias_bloqueio,
            detalhe=detalhe,
        )
        await responder_sucesso(
            interacao,
            titulo="Rebaixamento aplicado",
            linhas=[detalhe],
        )


async def abrir_rebaixamento_para_membro(
    interacao: discord.Interaction,
    alvo: discord.Member,
) -> None:
    """Atalho usado pelo painel Gerenciar Membros (já tem o alvo)."""
    await abrir_fluxo_rebaixamento(interacao, alvo)


def _checar_revogar(interacao: discord.Interaction) -> discord.Member | None:
    if not isinstance(interacao.user, discord.Member):
        return None
    if not membro_pode_revogar_rebaixamento(interacao.user):
        return None
    return interacao.user


# ---------------------------------------------------------------------------
# Revogar rebaixamento ativo
# ---------------------------------------------------------------------------


class PainelBuscaRevogarRebaixamentoLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Busca membro com rebaixamento ativo para revogar."""

    def __init__(self) -> None:
        super().__init__(timeout=300)
        componentes: list = [
            discord.ui.TextDisplay(
                "# ♻️ Revogar rebaixamento\n"
                "> Busque o membro que possui **rebaixamento ativo**.\n"
                "Acesso: **EQUIPE・DIRETORIA GERAL**, **Responsavel HP** "
                "ou **Administrador**."
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
        ]
        linha_user = discord.ui.ActionRow()
        seletor = discord.ui.UserSelect(
            placeholder="Selecione o membro…",
            min_values=1,
            max_values=1,
        )
        seletor.callback = self._ao_user_select
        linha_user.add_item(seletor)
        componentes.append(linha_user)

        linha_busca = discord.ui.ActionRow()
        b_discord = discord.ui.Button(
            label="Discord ID",
            style=discord.ButtonStyle.secondary,
            emoji="🔍",
        )
        b_discord.callback = self._ao_discord_id
        b_fivem = discord.ui.Button(
            label="FiveM",
            style=discord.ButtonStyle.secondary,
            emoji="🪪",
        )
        b_fivem.callback = self._ao_fivem
        linha_busca.add_item(b_discord)
        linha_busca.add_item(b_fivem)
        componentes.append(linha_busca)

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.dark_green(),
            )
        )

    async def _ao_user_select(self, interacao: discord.Interaction) -> None:
        if _checar_revogar(interacao) is None:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("revogar rebaixamento")],
            )
            return
        valores = interacao.data.get("values") if interacao.data else None
        if not valores:
            await responder_erro(
                interacao, titulo="Seleção", linhas=["Nenhum membro selecionado."]
            )
            return
        membro_id = int(valores[0])
        membro = interacao.guild.get_member(membro_id) if interacao.guild else None
        if membro is None:
            await responder_erro(
                interacao,
                titulo="Fora do servidor",
                linhas=["O membro precisa estar no servidor."],
            )
            return
        await abrir_lista_rebaixamentos_ativos(interacao, membro)

    async def _ao_discord_id(self, interacao: discord.Interaction) -> None:
        if _checar_revogar(interacao) is None:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("revogar rebaixamento")],
            )
            return
        await interacao.response.send_modal(ModalBuscarDiscordRevogar())

    async def _ao_fivem(self, interacao: discord.Interaction) -> None:
        if _checar_revogar(interacao) is None:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("revogar rebaixamento")],
            )
            return
        await interacao.response.send_modal(ModalBuscarFivemRevogar())


class ModalBuscarDiscordRevogar(LoggingModalMixin, discord.ui.Modal, title="Discord ID"):
    discord_id_input = discord.ui.TextInput(
        label="Discord ID", required=True, max_length=20
    )

    async def on_submit(self, interacao: discord.Interaction) -> None:
        if _checar_revogar(interacao) is None:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("revogar rebaixamento")],
            )
            return
        raw = self.discord_id_input.value.strip()
        if not raw.isdigit():
            await responder_erro(
                interacao, titulo="ID inválido", linhas=["Somente números."]
            )
            return
        membro_id = int(raw)
        membro = interacao.guild.get_member(membro_id) if interacao.guild else None
        if membro is None:
            await responder_erro(
                interacao,
                titulo="Fora do servidor",
                linhas=[f"<@{membro_id}> precisa estar no servidor."],
            )
            return
        await abrir_lista_rebaixamentos_ativos(interacao, membro)


class ModalBuscarFivemRevogar(LoggingModalMixin, discord.ui.Modal, title="ID FiveM"):
    id_fivem_input = discord.ui.TextInput(
        label="ID FiveM", required=True, max_length=20
    )

    async def on_submit(self, interacao: discord.Interaction) -> None:
        if _checar_revogar(interacao) is None:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("revogar rebaixamento")],
            )
            return
        valor = self.id_fivem_input.value.strip()
        if not valor.isdigit():
            await responder_erro(
                interacao, titulo="ID inválido", linhas=["Somente números."]
            )
            return
        async with async_session() as sessao:
            resultado = await sessao.execute(
                select(Usuario).where(Usuario.id_fivem == valor)
            )
            usuario = resultado.scalar_one_or_none()
        if usuario is None:
            await responder_erro(
                interacao,
                titulo="Não encontrado",
                linhas=[f"FiveM `{valor}` não cadastrado."],
            )
            return
        membro = (
            interacao.guild.get_member(usuario.discord_id) if interacao.guild else None
        )
        if membro is None:
            await responder_erro(
                interacao,
                titulo="Fora do servidor",
                linhas=[f"FiveM `{valor}` → <@{usuario.discord_id}> fora do servidor."],
            )
            return
        await abrir_lista_rebaixamentos_ativos(interacao, membro)


async def abrir_lista_rebaixamentos_ativos(
    interacao: discord.Interaction,
    alvo: discord.Member,
) -> None:
    """Lista rebaixamentos ativos do membro e oferece para escolher."""
    ativas = await listar_rebaixamentos_ativos(alvo.id, limite=25)
    if not ativas:
        await responder_erro(
            interacao,
            titulo="Nenhum rebaixamento ativo",
            linhas=[
                f"{alvo.mention} não possui rebaixamento ativo no banco.",
            ],
        )
        return

    opcoes: list[discord.SelectOption] = []
    for punicao in ativas:
        hist = await obter_historico_do_rebaixamento(punicao.id)
        if hist and hist.cargo_de and hist.cargo_para:
            resumo = f"{hist.cargo_de} → {hist.cargo_para}"
        else:
            resumo = (punicao.cargo_nome or "Rebaixamento").strip()
        quando = (
            f"<t:{int(punicao.criada_em.timestamp())}:d>"
            if punicao.criada_em
            else "—"
        )
        opcoes.append(
            discord.SelectOption(
                label=f"#{punicao.id} · {resumo}"[:100],
                value=str(punicao.id),
                description=f"{quando} · {(punicao.motivo or '')[:70]}"[:100],
                emoji="⬇️",
            )
        )

    await responder_view(
        interacao,
        PainelListaRevogarRebaixamentoLayout(alvo=alvo, opcoes=opcoes, ativas=ativas),
        ephemeral=True,
    )


class PainelListaRevogarRebaixamentoLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Select do rebaixamento ativo a revogar."""

    def __init__(
        self,
        *,
        alvo: discord.Member,
        opcoes: list[discord.SelectOption],
        ativas: list,
    ) -> None:
        super().__init__(timeout=300)
        self.alvo = alvo
        self.ativas = ativas

        linhas = []
        for punicao in ativas[:10]:
            linhas.append(
                f"• `#{punicao.id}` **{(punicao.cargo_nome or 'Rebaixamento').strip()}** — "
                f"{(punicao.motivo or '')[:80]}"
            )
        lista = "\n".join(linhas)

        componentes: list = [
            discord.ui.TextDisplay(
                f"# ♻️ Rebaixamentos ativos — {alvo.display_name}\n"
                f"{alvo.mention} (`{alvo.id}`)\n\n"
                f"{lista}"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
        ]
        linha = discord.ui.ActionRow()
        seletor = discord.ui.Select(
            placeholder="Escolha o rebaixamento a revogar…",
            options=opcoes,
            min_values=1,
            max_values=1,
        )
        seletor.callback = self._ao_escolher
        linha.add_item(seletor)
        componentes.append(linha)

        self.add_item(
            discord.ui.Container(
                *componentes,
                accent_color=discord.Color.dark_green(),
            )
        )

    async def _ao_escolher(self, interacao: discord.Interaction) -> None:
        if _checar_revogar(interacao) is None:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("revogar rebaixamento")],
            )
            return
        valores = interacao.data.get("values") if interacao.data else None
        if not valores:
            await responder_erro(
                interacao, titulo="Seleção", linhas=["Escolha um registro."]
            )
            return
        punicao_id = int(valores[0])
        await interacao.response.send_modal(
            ModalMotivoRevogarRebaixamento(
                alvo=self.alvo,
                punicao_id=punicao_id,
            )
        )


class ModalMotivoRevogarRebaixamento(
    LoggingModalMixin, discord.ui.Modal, title="Revogar rebaixamento"
):
    motivo_input = discord.ui.TextInput(
        label="Motivo da revogação",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500,
        placeholder="Por que este rebaixamento está sendo revogado?",
    )

    def __init__(self, *, alvo: discord.Member, punicao_id: int) -> None:
        super().__init__()
        self.alvo = alvo
        self.punicao_id = punicao_id

    async def on_submit(self, interacao: discord.Interaction) -> None:
        executor = _checar_revogar(interacao)
        if executor is None or interacao.guild is None:
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=[mensagem_sem_permissao("revogar rebaixamento")],
            )
            return
        motivo = (self.motivo_input.value or "").strip()
        if not motivo:
            await responder_erro(
                interacao, titulo="Motivo", linhas=["Informe o motivo da revogação."]
            )
            return

        alvo = interacao.guild.get_member(self.alvo.id)
        if alvo is None:
            await responder_erro(
                interacao,
                titulo="Alvo",
                linhas=["O membro saiu do servidor."],
            )
            return

        await interacao.response.defer(ephemeral=True)
        ok, detalhe = await revogar_rebaixamento(
            guild=interacao.guild,
            alvo=alvo,
            executor=executor,
            punicao_id=self.punicao_id,
            motivo=motivo,
        )
        if not ok:
            await responder_erro(interacao, titulo="Falha", linhas=[detalhe])
            return

        await publicar_log_revogacao_rebaixamento(
            interacao.guild,
            alvo=alvo,
            executor=executor,
            punicao_id=self.punicao_id,
            motivo=motivo,
            detalhe=detalhe,
        )
        await responder_sucesso(
            interacao,
            titulo="Rebaixamento revogado",
            linhas=[detalhe],
        )
