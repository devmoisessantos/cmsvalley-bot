"""Painel e botões de solicitação / aprovação de promoção."""

from __future__ import annotations

import discord

from src.config import CANAIS
from src.cursos.cursos_service import rotulo_curso
from src.plantao.permissoes import e_diretoria
from src.promocoes.promocoes_service import (
    aplicar_promocao_cargos,
    criar_solicitacao_promocao,
    decidir_solicitacao,
    listar_trilhas,
    montar_checklist_trilha,
    obter_solicitacao,
    obter_trilha,
    registrar_historico,
    atualizar_mensagem_solicitacao,
)
from src.utils.error_handling import (
    LoggingViewMixin,
    enviar_erro_para_log_erros,
)
from src.utils.log_container import LogContainerView
from src.utils.mensagens import (
    COR_ERRO,
    COR_INFO,
    COR_SUCESSO,
    responder_aviso,
    responder_erro,
    responder_sucesso,
)

CUSTOM_ID_SELECT_TRILHA = "promocoes:select_trilha"
CUSTOM_ID_APROVAR = "promocoes:aprovar:"
CUSTOM_ID_REPROVAR = "promocoes:reprovar:"


class PainelPromocaoLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Painel persistente — solicitar promoção."""

    def __init__(self, guild: discord.Guild | None = None):
        super().__init__(timeout=None)

        opcoes = [
            discord.SelectOption(
                label=trilha["rotulo"][:100],
                value=trilha["chave"],
                description=(trilha.get("observacao") or "")[:100],
            )
            for trilha in listar_trilhas()
        ][:25]

        linha = discord.ui.ActionRow()
        seletor = discord.ui.Select(
            placeholder="Escolha a promoção desejada…",
            options=opcoes,
            custom_id=CUSTOM_ID_SELECT_TRILHA,
            min_values=1,
            max_values=1,
        )
        seletor.callback = self._ao_escolher_trilha
        linha.add_item(seletor)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# ⬆️ Solicitar Promoção\n"
                    "Escolha a trilha. O bot verifica **advertências**, "
                    "**cargo atual** e **cursos obrigatórios**.\n"
                    "Se faltar curso, a solicitação **não** segue para a diretoria — "
                    "você recebe o que falta e pode ir ao painel de cursos.\n"
                    "-# Não promovemos com Adv 01 ou Adv 02 ativa."
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                linha,
                accent_color=discord.Color.blurple(),
            )
        )

    async def _ao_escolher_trilha(self, interacao: discord.Interaction):
        try:
            valores = interacao.data.get("values") if interacao.data else None
            if not valores:
                await responder_erro(
                    interacao,
                    titulo="Seleção inválida",
                    linhas=["Nenhuma trilha selecionada."],
                )
                return
            await processar_escolha_trilha(interacao, valores[0])
        except Exception as erro:
            await enviar_erro_para_log_erros(
                interacao.guild,
                "Erro ao processar trilha de promoção",
                erro,
                contexto="PainelPromocaoLayout._ao_escolher_trilha",
                usuario=interacao.user,
            )
            await responder_erro(
                interacao,
                titulo="Erro inesperado",
                linhas=["Falha na solicitação. A equipe foi notificada."],
            )


class ViewDecisaoPromocao(LoggingViewMixin, discord.ui.LayoutView):
    """Botões Aprovar / Reprovar no canal da diretoria."""

    def __init__(self, solicitacao_id: int, *, desabilitada: bool = False):
        super().__init__(timeout=None)
        self.solicitacao_id = solicitacao_id

        linha = discord.ui.ActionRow()
        botao_ok = discord.ui.Button(
            label="Aprovar",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"{CUSTOM_ID_APROVAR}{solicitacao_id}",
            disabled=desabilitada,
        )
        botao_ok.callback = self._ao_aprovar
        botao_nao = discord.ui.Button(
            label="Reprovar",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"{CUSTOM_ID_REPROVAR}{solicitacao_id}",
            disabled=desabilitada,
        )
        botao_nao.callback = self._ao_reprovar
        linha.add_item(botao_ok)
        linha.add_item(botao_nao)
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay("-# Ação da diretoria"),
                linha,
                accent_color=discord.Color.dark_gold(),
            )
        )

    async def _ao_aprovar(self, interacao: discord.Interaction):
        await self._decidir(interacao, aprovada=True)

    async def _ao_reprovar(self, interacao: discord.Interaction):
        await self._decidir(interacao, aprovada=False)

    async def _decidir(self, interacao: discord.Interaction, *, aprovada: bool):
        membro = interacao.user
        if not isinstance(membro, discord.Member) or not e_diretoria(membro):
            await responder_erro(
                interacao,
                titulo="Sem permissão",
                linhas=["Apenas **Diretoria+** pode decidir promoções."],
            )
            return

        try:
            await interacao.response.defer(ephemeral=True)
            registro = await obter_solicitacao(self.solicitacao_id)
            if registro is None:
                await responder_erro(
                    interacao,
                    titulo="Pedido não encontrado",
                    linhas=[f"ID `{self.solicitacao_id}` não existe."],
                )
                return
            if registro.status != "PENDENTE":
                await responder_aviso(
                    interacao,
                    titulo="Já decidido",
                    linhas=[f"Status atual: `{registro.status}`."],
                    delay=10,
                )
                return

            registro = await decidir_solicitacao(
                solicitacao_id=self.solicitacao_id,
                aprovada=aprovada,
                analisado_por=membro.id,
                motivo=None if aprovada else "Reprovado pela diretoria",
            )
            if registro is None:
                await responder_erro(
                    interacao,
                    titulo="Falha",
                    linhas=["Não foi possível atualizar o pedido."],
                )
                return

            guilda = interacao.guild
            alvo = guilda.get_member(registro.discord_id) if guilda else None

            if aprovada and alvo is not None:
                ok, detalhe = await aplicar_promocao_cargos(
                    alvo, registro.cargo_de, registro.cargo_para
                )
                if not ok:
                    await enviar_erro_para_log_erros(
                        guilda,
                        "Promoção aprovada mas falha ao dar cargo",
                        RuntimeError(detalhe),
                        contexto="ViewDecisaoPromocao.aplicar_promocao",
                        usuario=membro,
                    )
                    await responder_erro(
                        interacao,
                        titulo="Aprovado no sistema, falha nos cargos",
                        linhas=[detalhe, "Ajuste os cargos manualmente se preciso."],
                    )
                    # ainda registra histórico
                await registrar_historico(
                    discord_id=registro.discord_id,
                    tipo="PROMOCAO",
                    cargo_de=registro.cargo_de,
                    cargo_para=registro.cargo_para,
                    motivo="Aprovado pela diretoria",
                    executado_por=membro.id,
                    solicitacao_id=registro.id,
                )
                await _postar_resultado_publico(
                    guilda,
                    aprovada=True,
                    alvo_id=registro.discord_id,
                    cargo_de=registro.cargo_de,
                    cargo_para=registro.cargo_para,
                    staff=membro,
                    solicitacao_id=registro.id,
                )
            else:
                await registrar_historico(
                    discord_id=registro.discord_id,
                    tipo="NAO_PROMOVIDO",
                    cargo_de=registro.cargo_de,
                    cargo_para=registro.cargo_para,
                    motivo="Reprovado pela diretoria",
                    executado_por=membro.id,
                    solicitacao_id=registro.id,
                )
                await _postar_resultado_publico(
                    guilda,
                    aprovada=False,
                    alvo_id=registro.discord_id,
                    cargo_de=registro.cargo_de,
                    cargo_para=registro.cargo_para,
                    staff=membro,
                    solicitacao_id=registro.id,
                )

            # Desativa botões na mensagem
            try:
                await interacao.message.edit(
                    view=ViewDecisaoPromocao(self.solicitacao_id, desabilitada=True)
                )
            except discord.HTTPException:
                pass

            await responder_sucesso(
                interacao,
                titulo="Promoção aprovada" if aprovada else "Promoção reprovada",
                linhas=[
                    f"Pedido `#{registro.id}` marcado como "
                    f"**{'APROVADA' if aprovada else 'REPROVADA'}**."
                ],
                delay=12,
            )
        except Exception as erro:
            await enviar_erro_para_log_erros(
                interacao.guild,
                "Erro ao decidir promoção",
                erro,
                contexto="ViewDecisaoPromocao._decidir",
                usuario=membro,
            )
            await responder_erro(
                interacao,
                titulo="Erro inesperado",
                linhas=["Falha ao processar a decisão. Veja LOG_ERROS."],
            )


async def processar_escolha_trilha(
    interacao: discord.Interaction,
    chave_trilha: str,
) -> None:
    trilha = obter_trilha(chave_trilha)
    if trilha is None:
        await responder_erro(
            interacao,
            titulo="Trilha inválida",
            linhas=[f"`{chave_trilha}` não existe no regulamento configurado."],
        )
        return

    membro = interacao.user
    if not isinstance(membro, discord.Member):
        await responder_erro(
            interacao,
            titulo="Apenas no servidor",
            linhas=["Use o painel dentro do Discord do hospital."],
        )
        return

    checklist = montar_checklist_trilha(membro, trilha)

    if not checklist["ok"]:
        linhas = list(checklist["linhas"])
        faltando = checklist.get("cursos_faltando") or []
        if faltando:
            linhas.append(
                "Cursos pendentes: "
                + ", ".join(f"`{rotulo_curso(c)}`" for c in faltando)
            )
            canal_cursos = CANAIS.get("CANAL_PAINEL_SOLICITAR_CURSOS")
            if canal_cursos:
                linhas.append(f"Abra <#{canal_cursos}> para solicitar o curso.")
        await responder_erro(
            interacao,
            titulo=f"Não pode solicitar · {checklist['rotulo']}",
            linhas=linhas,
        )
        return

    resumo = "\n".join(checklist["linhas"])
    registro = await criar_solicitacao_promocao(
        discord_id=membro.id,
        trilha=trilha,
        resumo_checklist=resumo,
    )

    guilda = interacao.guild
    canal_dest_id = (
        CANAIS.get("CANAL_PROMOVIDOS")
        or CANAIS.get("LOG_PROMOVIDOS")
    )
    # Pedidos pendentes: usamos CANAL_PROMOVIDOS como fila da diretoria
    # (ou LOG). Se preferir canal só de fila, ajuste no config depois.
    canal = guilda.get_channel(int(canal_dest_id)) if guilda and canal_dest_id else None

    corpo = (
        f"👤 {membro.mention} (`{membro.id}`)\n"
        f"📋 Pedido `#{registro.id}`\n"
        f"**Trilha:** {checklist['rotulo']}\n"
        f"**De:** `{checklist['cargo_de']}` → **Para:** `{checklist['cargo_para']}`\n\n"
        f"{resumo}"
    )

    if canal is not None:
        try:
            view_pedido = _ViewPedidoPromocao(
                titulo=f"Solicitação de promoção · #{registro.id}",
                corpo=corpo,
                guild=guilda,
                solicitacao_id=registro.id,
            )
            mensagem = await canal.send(view=view_pedido)
            await atualizar_mensagem_solicitacao(
                registro.id, canal.id, mensagem.id
            )
        except discord.HTTPException as erro:
            await enviar_erro_para_log_erros(
                guilda,
                "Falha ao postar pedido de promoção",
                erro,
                contexto="processar_escolha_trilha.send",
                usuario=membro,
            )
            await responder_erro(
                interacao,
                titulo="Falha ao enviar à diretoria",
                linhas=["Pedido salvo no banco, mas não postou no canal. Veja LOG_ERROS."],
            )
            return
    else:
        await enviar_erro_para_log_erros(
            guilda,
            "Canal de promoções não configurado",
            RuntimeError("CANAL_PROMOVIDOS ausente"),
            contexto="processar_escolha_trilha",
            usuario=membro,
        )

    await responder_sucesso(
        interacao,
        titulo="Solicitação enviada",
        linhas=[
            f"Pedido `#{registro.id}` · **{checklist['rotulo']}**",
            "A diretoria vai analisar Aprovar / Reprovar no canal de promoções.",
            *checklist["linhas"][:4],
        ],
        delay=25,
    )


class _ViewPedidoPromocao(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(
        self,
        *,
        titulo: str,
        corpo: str,
        guild: discord.Guild,
        solicitacao_id: int,
    ):
        super().__init__(timeout=None)
        linha = discord.ui.ActionRow()
        botao_ok = discord.ui.Button(
            label="Aprovar",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"{CUSTOM_ID_APROVAR}{solicitacao_id}",
        )
        botao_ok.callback = self._make_cb(solicitacao_id, True)
        botao_nao = discord.ui.Button(
            label="Reprovar",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"{CUSTOM_ID_REPROVAR}{solicitacao_id}",
        )
        botao_nao.callback = self._make_cb(solicitacao_id, False)
        linha.add_item(botao_ok)
        linha.add_item(botao_nao)

        from datetime import datetime, timezone

        momento = int(datetime.now(timezone.utc).timestamp())
        rodape = f"-# {guild.name} • <t:{momento}:f>"

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(f"# {titulo}"),
                discord.ui.TextDisplay(corpo),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
                linha,
                discord.ui.TextDisplay(rodape),
                accent_color=discord.Color.gold(),
            )
        )

    def _make_cb(self, solicitacao_id: int, aprovada: bool):
        async def _cb(interacao: discord.Interaction):
            proxy = ViewDecisaoPromocao(solicitacao_id)
            await proxy._decidir(interacao, aprovada=aprovada)

        return _cb


async def _postar_resultado_publico(
    guilda: discord.Guild | None,
    *,
    aprovada: bool,
    alvo_id: int,
    cargo_de: str,
    cargo_para: str,
    staff: discord.Member,
    solicitacao_id: int,
) -> None:
    if guilda is None:
        return
    canal_id = CANAIS.get("LOG_PROMOVIDOS") or CANAIS.get("CANAL_PROMOVIDOS")
    if not aprovada:
        canal_id = (
            CANAIS.get("CANAL_NAO_PROMOVIDOS")
            or CANAIS.get("LOG_PROMOVIDOS")
            or canal_id
        )
    canal = guilda.get_channel(int(canal_id)) if canal_id else None
    if canal is None:
        return
    titulo = "Promoção aprovada" if aprovada else "Promoção não aprovada"
    linhas = (
        f"👤 <@{alvo_id}>\n"
        f"📋 Pedido `#{solicitacao_id}`\n"
        f"**{cargo_de}** → **{cargo_para}**\n"
        f"Staff: {staff.mention}"
    )
    try:
        await canal.send(
            view=LogContainerView(
                titulo=titulo,
                linhas=linhas,
                guild=guilda,
                cor=COR_SUCESSO if aprovada else COR_ERRO,
            )
        )
    except discord.HTTPException as erro:
        await enviar_erro_para_log_erros(
            guilda,
            "Falha ao postar resultado de promoção",
            erro,
            contexto="_postar_resultado_publico",
            usuario=staff,
        )


def view_persistente_promocao() -> PainelPromocaoLayout:
    return PainelPromocaoLayout()
