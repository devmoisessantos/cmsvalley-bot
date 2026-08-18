"""Comandos administrativos do baú + painel ephemeral."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from src.bau.bau_service import (
    chave_ciclo_atual,
    liberar_limite_manual,
    obter_limites_camada_1,
    obter_limites_camada_2,
    obter_tolerancia_extra,
    salvar_config_bau,
)
from src.database.conexao import async_session
from src.database.models import (
    AdvertenciaVerbalBau,
    CasoBau,
    ContadorItemBau,
)
from src.manutencao.manutencao_paineis import recriar_painel
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
from src.utils.permissions import (
    apenas_administrador,
    is_authorized,
)


class BauCog(commands.Cog):
    grupo_bau = app_commands.Group(
        name="bau",
        description="Monitoramento e administração do baú do hospital",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @grupo_bau.command(
        name="publicar",
        description="[Admin] Publica ou recria o painel de controle no "
        "CANAL_PAINEL_BAU",
    )
    @apenas_administrador()
    async def publicar(self, interacao: discord.Interaction):
        """Reconstrói o painel administrativo do baú no canal configurado.

        Delega a recriação ao mecanismo central de painéis para não deixar uma
        mensagem antiga com componentes inválidos. Responde ao administrador de
        forma privada com o resultado da publicação no Discord.
        """
        await interacao.response.defer(ephemeral=True)
        resultado = await recriar_painel(self.bot, "bau")
        if resultado.ok:
            await responder_sucesso(
                interacao,
                titulo="Painel do baú",
                linhas=[resultado.mensagem],
            )
        else:
            await responder_erro(
                interacao,
                titulo="Falha ao publicar painel",
                linhas=[resultado.mensagem],
            )

    @grupo_bau.command(
        name="painel",
        description="[Admin] Painel ephemeral de gestão avançada do baú",
    )
    @apenas_administrador()
    async def painel(self, interacao: discord.Interaction):
        """Abre os controles avançados apenas para o administrador que pediu.

        A visualização recebe o identificador do solicitante para impedir que
        outra pessoa use os botões de uma sessão administrativa privada.
        """
        await responder_view(
            interacao,
            PainelAdminBauView(interacao.user.id),
            ephemeral=True,
        )

    @grupo_bau.command(
        name="liberar",
        description="Zera contador e fecha casos de um item (autorização pontual)",
    )
    @app_commands.describe(
        id_fivem="Passaporte FiveM",
        item="Item canônico (ex: celular, roupas, repairkit)",
    )
    @is_authorized()
    async def liberar(
        self,
        interacao: discord.Interaction,
        id_fivem: str,
        item: str,
    ):
        """Autoriza excepcionalmente uma nova contagem de um item para alguém.

        Confere se o item informado pertence aos limites conhecidos antes de
        delegar a liberação. A operação altera os registros do baú e vincula o
        administrador executor, evitando liberações para nomes digitados de
        forma incompatível com a configuração.
        """
        item_limpo = item.strip().lower()
        limites = await obter_limites_camada_1()
        if item_limpo not in limites:
            await responder_erro(
                interacao,
                titulo="Item inválido",
                linhas=[
                    f"`{item}` não está nos limites configurados.",
                    "Itens: " + ", ".join(sorted(limites.keys())),
                ],
            )
            return
        mensagem = await liberar_limite_manual(
            id_fivem=id_fivem.strip(),
            item_canonico=item_limpo,
            executor_id=interacao.user.id,
        )
        await responder_sucesso(interacao, titulo="Limite liberado", linhas=[mensagem])

    @grupo_bau.command(
        name="ciclo",
        description="Mostra a chave do ciclo de contagem atual",
    )
    async def ciclo(self, interacao: discord.Interaction):
        """Explica o período de contagem vigente e sua margem de alerta.

        A resposta privada também esclarece que a reinicialização dos
        contadores não apaga casos pendentes, evitando a impressão de que uma
        ocorrência deixou de exigir acompanhamento.
        """
        tolerancia = await obter_tolerancia_extra()
        await responder_info(
            interacao,
            titulo="Ciclo atual do baú",
            linhas=[
                f"Chave: `{chave_ciclo_atual()}`",
                f"Tolerância extra: **+{tolerancia}** (alerta só acima de "
                f"limite+{tolerancia})",
                "Resets locais: 00:00, 11:00 e 17:00.",
                "Casos abertos **não** são apagados no reset.",
            ],
        )


class PainelAdminBauView(LoggingViewMixin, discord.ui.LayoutView):
    """Painel ephemeral: 3 botões + select de gestão."""

    def __init__(self, id_admin: int):
        super().__init__(timeout=300)
        self.id_admin = id_admin

        linha_botoes = discord.ui.ActionRow()
        for label, emoji, estilo, cb_name, cid in [
            (
                "Casos abertos",
                "📋",
                discord.ButtonStyle.primary,
                "_ao_casos",
                "bau_adm:casos",
            ),
            (
                "Contadores ciclo",
                "📊",
                discord.ButtonStyle.secondary,
                "_ao_contadores",
                "bau_adm:contadores",
            ),
            (
                "Ver limites",
                "⚙️",
                discord.ButtonStyle.secondary,
                "_ao_limites",
                "bau_adm:limites",
            ),
        ]:
            botao = discord.ui.Button(
                label=label, emoji=emoji, style=estilo, custom_id=cid
            )
            botao.callback = getattr(self, cb_name)
            linha_botoes.add_item(botao)

        opcoes = [
            discord.SelectOption(
                label="Editar tolerância (+N)",
                value="tol",
                description="Alerta só acima de limite+N",
            ),
            discord.SelectOption(label="Editar limite diário (camada 1)", value="l1"),
            discord.SelectOption(label="Editar limite grave (camada 2)", value="l2"),
            discord.SelectOption(label="Listar verbais (prontuário)", value="verbais"),
            discord.SelectOption(label="Consultar casos por status", value="status"),
            discord.SelectOption(label="Liberar item (passaporte)", value="liberar"),
        ]
        seletor = discord.ui.Select(
            placeholder="Mais ações de gestão…",
            options=opcoes,
            min_values=1,
            max_values=1,
            custom_id="bau_adm:menu",
        )
        seletor.callback = self._ao_select
        linha_select = discord.ui.ActionRow()
        linha_select.add_item(seletor)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    "# 📦 Painel Admin — Baú\n"
                    "Gestão de limites, casos, contadores e tolerância.\n"
                    f"Ciclo atual: `{chave_ciclo_atual()}`"
                ),
                linha_botoes,
                linha_select,
                accent_color=discord.Color.dark_gold(),
            )
        )

    def _so_admin(self, interacao: discord.Interaction) -> bool:
        return interacao.user.id == self.id_admin

    async def _ao_casos(self, interacao: discord.Interaction):
        if not self._so_admin(interacao):
            await responder_erro(
                interacao, titulo="Sem permissão", linhas=["Painel de outro admin."]
            )
            return
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
                interacao, titulo="Casos abertos", linhas=["Nenhum caso aberto."]
            )
            return
        linhas = [
            f"• `#{caso.id}` · `{caso.id_fivem}` · **{caso.quantidade_atual}** un. · "
            f"`{caso.status}`"
            for caso in casos
        ]
        await responder_info(
            interacao, titulo=f"Casos abertos ({len(casos)})", linhas=linhas, delay=60
        )

    async def _ao_contadores(self, interacao: discord.Interaction):
        if not self._so_admin(interacao):
            await responder_erro(
                interacao, titulo="Sem permissão", linhas=["Painel de outro admin."]
            )
            return
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
                linhas=[f"Ciclo `{ciclo}` sem movimentação."],
            )
            return
        linhas = [
            f"• `{linha_do_banco.id_fivem}` · **{linha_do_banco.item_canonico}** "
            f"x{linha_do_banco.quantidade} "
            f"· {linha_do_banco.nome_cidade or '—'}"
            for linha_do_banco in rows
        ]
        await responder_info(
            interacao, titulo=f"Contadores · {ciclo}", linhas=linhas, delay=60
        )

    async def _ao_limites(self, interacao: discord.Interaction):
        if not self._so_admin(interacao):
            await responder_erro(
                interacao, titulo="Sem permissão", linhas=["Painel de outro admin."]
            )
            return
        l1 = await obter_limites_camada_1()
        l2 = await obter_limites_camada_2()
        tol = await obter_tolerancia_extra()
        linhas = [f"**Tolerância extra:** +{tol} (alerta se qtd > limite+{tol})", ""]
        linhas.append("**Camada 1 (diário)**")
        for item, valor in sorted(l1.items()):
            teto = valor + tol
            linhas.append(
                f"• `{item}`: limite **{valor}** → sem alerta até **{teto}** · "
                f"alerta **{teto + 1}+**"
            )
        linhas.append("")
        linhas.append("**Camada 2 (grave)**")
        for item, valor in sorted(l2.items()):
            linhas.append(f"• `{item}`: **{valor}**")
        await responder_info(
            interacao, titulo="Limites do baú", linhas=linhas, delay=90
        )

    async def _ao_select(self, interacao: discord.Interaction):
        if not self._so_admin(interacao):
            await responder_erro(
                interacao, titulo="Sem permissão", linhas=["Painel de outro admin."]
            )
            return
        valor = interacao.data["values"][0] if interacao.data else ""
        if valor == "tol":
            await interacao.response.send_modal(ModalEditarTolerancia())
        elif valor == "l1":
            await interacao.response.send_modal(ModalEditarLimite(camada=1))
        elif valor == "l2":
            await interacao.response.send_modal(ModalEditarLimite(camada=2))
        elif valor == "verbais":
            async with async_session() as sessao:
                resultado = await sessao.execute(
                    select(AdvertenciaVerbalBau)
                    .order_by(AdvertenciaVerbalBau.id.desc())
                    .limit(15)
                )
                registros = list(resultado.scalars().all())
            if not registros:
                await responder_info(
                    interacao, titulo="Verbais", linhas=["Nenhuma verbal registrada."]
                )
                return
            linhas = [
                f"• `#{registro.id}` · `{registro.id_fivem}` · `{registro.tipo}` · "
                f"{registro.item_canonico or '—'} "
                f"· {registro.motivo[:60]}"
                for registro in registros
            ]
            await responder_info(
                interacao, titulo="Prontuário verbal", linhas=linhas, delay=60
            )
        elif valor == "status":
            await interacao.response.send_modal(ModalConsultarStatus())
        elif valor == "liberar":
            await interacao.response.send_modal(ModalLiberarItem())
        else:
            await responder_aviso_safe(interacao, "Opção desconhecida.")


async def responder_aviso_safe(interacao, texto: str):
    """Envia um aviso padronizado do baú pela resposta centralizada."""
    from src.utils.mensagens import responder_aviso

    await responder_aviso(interacao, titulo="Baú", linhas=[texto])


class ModalEditarTolerancia(
    LoggingModalMixin, discord.ui.Modal, title="Tolerância extra"
):
    valor = discord.ui.TextInput(
        label="Tolerância (+N além do limite diário)",
        placeholder="1",
        required=True,
        max_length=2,
    )

    async def on_submit(self, interacao: discord.Interaction):
        """Valida e persiste a margem adicional usada antes de abrir alertas.

        Recusa valores que não sejam inteiros não negativos para impedir uma
        configuração ambígua. Ao salvar no banco, registra quem fez a mudança
        e mostra o impacto prático da nova tolerância.
        """
        if not self.valor.value.strip().isdigit():
            await responder_erro(
                interacao,
                titulo="Valor inválido",
                linhas=["Use um número inteiro ≥ 0."],
            )
            return
        numero = int(self.valor.value.strip())
        await salvar_config_bau(
            "tolerancia_extra", str(numero), atualizado_por=interacao.user.id
        )
        await responder_sucesso(
            interacao,
            titulo="Tolerância salva",
            linhas=[
                f"Tolerância = **+{numero}**.",
                f"Ex.: limite 1 → sem alerta até **{1 + numero}**; alerta a partir "
                f"de **{2 + numero}**.",
            ],
        )


class ModalEditarLimite(LoggingModalMixin, discord.ui.Modal, title="Editar limite"):
    item = discord.ui.TextInput(
        label="Item canônico", placeholder="cristal", required=True, max_length=40
    )
    quantidade = discord.ui.TextInput(
        label="Novo limite", placeholder="10", required=True, max_length=4
    )

    def __init__(self, camada: int):
        super().__init__()
        self.camada = camada
        self.title = f"Editar limite camada {camada}"

    async def on_submit(self, interacao: discord.Interaction):
        """Atualiza no banco o limite da camada escolhida para um item.

        Normaliza o nome do item e aceita somente quantidades inteiras, pois a
        chave de configuração precisa coincidir com a consultada pelo monitor.
        A resposta confirma o valor salvo como substituição da configuração.
        """
        item = self.item.value.strip().lower()
        if not self.quantidade.value.strip().isdigit():
            await responder_erro(
                interacao, titulo="Quantidade inválida", linhas=["Use só números."]
            )
            return
        quantidade = int(self.quantidade.value.strip())
        chave = f"limite_{self.camada}_{item}"
        await salvar_config_bau(
            chave, str(quantidade), atualizado_por=interacao.user.id
        )
        await responder_sucesso(
            interacao,
            titulo="Limite salvo",
            linhas=[
                f"`{item}` camada {self.camada} = **{quantidade}** (override no banco)."
            ],
        )


class ModalConsultarStatus(
    LoggingModalMixin, discord.ui.Modal, title="Casos por status"
):
    status = discord.ui.TextInput(
        label="Status",
        placeholder="AGUARDANDO | GRAVE | RESOLVIDO | IGNORADO | PUNIDO",
        required=True,
        max_length=20,
    )

    async def on_submit(self, interacao: discord.Interaction):
        """Consulta os casos persistidos que têm o estado escolhido.

        Converte o estado para maiúsculas antes da busca para acompanhar o
        formato salvo no banco e retorna no máximo quinze ocorrências, evitando
        uma resposta do Discord grande demais.
        """
        st = self.status.value.strip().upper()
        async with async_session() as sessao:
            resultado = await sessao.execute(
                select(CasoBau)
                .where(CasoBau.status == st)
                .order_by(CasoBau.id.desc())
                .limit(15)
            )
            casos = list(resultado.scalars().all())
        if not casos:
            await responder_info(
                interacao, titulo=f"Status {st}", linhas=["Nenhum caso."]
            )
            return
        linhas = [
            f"• `#{caso.id}` · `{caso.id_fivem}` · **{caso.item_canonico}** "
            f"x{caso.quantidade_atual}"
            for caso in casos
        ]
        await responder_info(interacao, titulo=f"Casos · {st}", linhas=linhas, delay=60)


class ModalLiberarItem(LoggingModalMixin, discord.ui.Modal, title="Liberar item"):
    id_fivem = discord.ui.TextInput(
        label="Passaporte FiveM", required=True, max_length=20
    )
    item = discord.ui.TextInput(
        label="Item canônico", placeholder="celular", required=True, max_length=40
    )

    async def on_submit(self, interacao: discord.Interaction):
        """Executa a liberação pontual informada no formulário.

        A operação altera os contadores e casos relacionados no banco por meio
        do serviço central, mantendo o identificador de quem autorizou a ação.
        """
        item = self.item.value.strip().lower()
        mensagem_de_retorno = await liberar_limite_manual(
            id_fivem=self.id_fivem.value.strip(),
            item_canonico=item,
            executor_id=interacao.user.id,
        )
        await responder_sucesso(
            interacao, titulo="Liberado", linhas=[mensagem_de_retorno]
        )


async def setup(bot: commands.Bot):
    """Registra os comandos administrativos do baú no bot."""
    await bot.add_cog(BauCog(bot))
