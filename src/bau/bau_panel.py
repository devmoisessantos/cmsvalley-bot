"""Painel persistente de controle do baú da organização (Components V2)."""

from __future__ import annotations

import discord
from sqlalchemy import select

from src.bau.bau_service import (
    chave_ciclo_atual,
    liberar_limite_manual,
    obter_limites_camada_1,
    obter_limites_camada_2,
    obter_tolerancia_extra,
)
from src.database.connection import async_session
from src.database.models import (
    CasoBau,
    ContadorItemBau,
)
from src.utils.error_handling import (
    LoggingModalMixin,
    LoggingViewMixin,
)
from src.utils.mensagens import (
    responder_erro,
    responder_info,
    responder_sucesso,
    responder_view,
)


def _membro_autorizado(membro: discord.Member) -> bool:
    if membro.guild_permissions.administrator:
        return True
    # mesma lógica visual: quem passa no check is_authorized dos comandos
    # aqui só admin ou quem já usa painéis de punição/staff — admin flag cobre
    return False


TEXTO_PAINEL = (
    "# 📦 Controle do Baú — CMS Valley\n\n"
    "Central de monitoramento das **retiradas do baú do hospital**.\n\n"
    "**O que o sistema faz**\n"
    "• Escuta o canal de logs do baú 24/7\n"
    "• Conta retiradas por passaporte e por item no ciclo atual\n"
    "• Alerta só quando a quantidade passa de **limite diário + tolerância**\n"
    "• Abre casos com prazo de devolução e verbais em reincidência\n\n"
    "**Ciclo atual** reseta às **00:00**, **11:00** e **17:00** (horário local).\n"
    "Casos abertos **não** são apagados no reset.\n\n"
    "-# Use os botões abaixo para consultar e gerir. Config avançada: `/bau painel`."
)


class PainelBauLayout(LoggingViewMixin, discord.ui.LayoutView):
    """Painel fixo em CANAL_PAINEL_BAU."""

    def __init__(self, guild: discord.Guild | None = None):
        super().__init__(timeout=None)
        self.guild = guild
        icon_url = guild.icon.url if guild and guild.icon else None

        if icon_url:
            cabecalho = discord.ui.Section(
                TEXTO_PAINEL,
                accessory=discord.ui.Thumbnail(icon_url),
            )
        else:
            cabecalho = discord.ui.TextDisplay(TEXTO_PAINEL)

        linha1 = discord.ui.ActionRow()
        b_casos = discord.ui.Button(
            label="Casos abertos",
            style=discord.ButtonStyle.primary,
            emoji="📋",
            custom_id="bau:painel:casos",
        )
        b_casos.callback = self._ao_casos
        linha1.add_item(b_casos)

        b_cont = discord.ui.Button(
            label="Contadores do ciclo",
            style=discord.ButtonStyle.secondary,
            emoji="📊",
            custom_id="bau:painel:contadores",
        )
        b_cont.callback = self._ao_contadores
        linha1.add_item(b_cont)

        b_lim = discord.ui.Button(
            label="Ver limites",
            style=discord.ButtonStyle.secondary,
            emoji="⚙️",
            custom_id="bau:painel:limites",
        )
        b_lim.callback = self._ao_limites
        linha1.add_item(b_lim)

        linha2 = discord.ui.ActionRow()
        b_lib = discord.ui.Button(
            label="Liberar item",
            style=discord.ButtonStyle.success,
            emoji="🔓",
            custom_id="bau:painel:liberar",
        )
        b_lib.callback = self._ao_liberar
        linha2.add_item(b_lib)

        b_cfg = discord.ui.Button(
            label="Gestão admin",
            style=discord.ButtonStyle.danger,
            emoji="🛠️",
            custom_id="bau:painel:admin",
        )
        b_cfg.callback = self._ao_admin
        linha2.add_item(b_cfg)

        self.add_item(
            discord.ui.Container(
                cabecalho,
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                linha1,
                linha2,
                accent_color=discord.Color.dark_gold(),
            )
        )

    async def _ao_casos(self, interacao: discord.Interaction):
        async with async_session() as sessao:
            resultado = await sessao.execute(
                select(CasoBau)
                .where(CasoBau.status.in_(("AGUARDANDO", "GRAVE", "PRAZO_ESTOURADO")))
                .order_by(CasoBau.id.desc())
                .limit(15)
            )
            casos = list(resultado.scalars().all())
        if not casos:
            await responder_info(
                interacao,
                titulo="Casos abertos",
                linhas=["Nenhum caso aberto no momento."],
            )
            return
        linhas = [
            f"• `#{c.id}` · `{c.id_fivem}` · **{c.quantidade_atual}** un. · `{c.status}`"
            for c in casos
        ]
        await responder_info(
            interacao,
            titulo=f"Casos abertos ({len(casos)})",
            linhas=linhas,
            delay=45,
        )

    async def _ao_contadores(self, interacao: discord.Interaction):
        ciclo = chave_ciclo_atual()
        async with async_session() as sessao:
            resultado = await sessao.execute(
                select(ContadorItemBau)
                .where(ContadorItemBau.ciclo_chave == ciclo)
                .order_by(ContadorItemBau.quantidade.desc())
                .limit(20)
            )
            rows = list(resultado.scalars().all())
        if not rows:
            await responder_info(
                interacao,
                titulo="Contadores",
                linhas=[f"Ciclo `{ciclo}` ainda sem movimentação registrada."],
            )
            return
        linhas = [
            f"• `{r.id_fivem}` · **{r.item_canonico}** x{r.quantidade} · {r.nome_cidade or '—'}"
            for r in rows
        ]
        await responder_info(
            interacao,
            titulo=f"Contadores · ciclo `{ciclo}`",
            linhas=linhas,
            delay=45,
        )

    async def _ao_limites(self, interacao: discord.Interaction):
        l1 = await obter_limites_camada_1()
        l2 = await obter_limites_camada_2()
        tol = await obter_tolerancia_extra()
        linhas = [
            f"**Tolerância extra:** +{tol}",
            f"Alerta somente se quantidade **> limite + {tol}**",
            "",
            "**Limite diário (camada 1)**",
        ]
        for item, val in sorted(l1.items()):
            teto = val + tol
            linhas.append(
                f"• `{item}`: limite **{val}** · ok até **{teto}** · alerta **{teto + 1}+**"
            )
        linhas.append("")
        linhas.append("**Limite grave (camada 2)**")
        for item, val in sorted(l2.items()):
            linhas.append(f"• `{item}`: **{val}**")
        await responder_info(
            interacao,
            titulo="Limites do baú",
            linhas=linhas,
            delay=60,
        )

    async def _ao_liberar(self, interacao: discord.Interaction):
        if not isinstance(interacao.user, discord.Member):
            await responder_erro(
                interacao,
                titulo="Contexto inválido",
                linhas=["Use o painel dentro do servidor."],
            )
            return
        if not interacao.user.guild_permissions.administrator:
            # permite quem tem permissão de gerenciar cargos como staff
            if not interacao.user.guild_permissions.manage_roles:
                await responder_erro(
                    interacao,
                    titulo="Sem permissão",
                    linhas=["Apenas staff autorizada pode liberar limites."],
                )
                return
        await interacao.response.send_modal(ModalLiberarDoPainel())

    async def _ao_admin(self, interacao: discord.Interaction):
        if not isinstance(interacao.user, discord.Member):
            await responder_erro(
                interacao,
                titulo="Contexto inválido",
                linhas=["Use o painel dentro do servidor."],
            )
            return
        if not interacao.user.guild_permissions.administrator:
            await responder_erro(
                interacao,
                titulo="Somente administradores",
                linhas=["A gestão avançada é restrita a administradores."],
            )
            return
        # import local evita ciclo com bau_cogs
        from src.bau.bau_cogs import PainelAdminBauView

        await responder_view(
            interacao,
            PainelAdminBauView(interacao.user.id),
            ephemeral=True,
        )


class ModalLiberarDoPainel(
    LoggingModalMixin, discord.ui.Modal, title="🔓 Liberar item"
):
    id_fivem = discord.ui.TextInput(
        label="Passaporte FiveM",
        placeholder="65659",
        required=True,
        max_length=20,
    )
    item = discord.ui.TextInput(
        label="Item canônico",
        placeholder="celular",
        required=True,
        max_length=40,
    )

    async def on_submit(self, interacao: discord.Interaction):
        item = self.item.value.strip().lower()
        limites = await obter_limites_camada_1()
        if item not in limites:
            await responder_erro(
                interacao,
                titulo="Item inválido",
                linhas=["Itens: " + ", ".join(sorted(limites.keys()))],
            )
            return
        msg = await liberar_limite_manual(
            id_fivem=self.id_fivem.value.strip(),
            item_canonico=item,
            executor_id=interacao.user.id,
        )
        await responder_sucesso(interacao, titulo="Limite liberado", linhas=[msg])
