import asyncio
import logging
from datetime import (
    datetime,
    timezone,
)

import discord
from models import (
    Chamada,
    EstadoPlantao,
    Recrutamento,
    Usuario,
)
from sqlalchemy import select

from src.config import (
    CANAIS,
    CARGOS,
    CARGOS_BYPASS_PRESENCA_CHAMADA,
)
from src.database.connection import async_session
from src.plantao.chamada.chamada_service import (
    calcular_proximo_horario_permitido,
    finalizar_chamada,
    registrar_falta,
    tentar_iniciar_chamada,
)
from src.plantao.chamada.chamada_state import (
    MedicoNaChamada,
    SessaoChamada,
    definir_sessao,
    obter_sessao,
)
from src.plantao.ocr.ocr_ems_service import (
    OcrEmsError,
    extrair_medicos_do_print_ems,
)
from src.plantao.ocr.scraping_membros import (
    combinar_membros,
    construir_membros_via_apelido,
)
from src.plantao.plantao_service import (
    desligar_servico,
    membro_e_doutor_ou_acima,
)
from src.services.validacao_ids import (
    MembroConhecido,
    validar_medicos,
)
from src.utils.error_handling import LoggingViewMixin
from src.utils.mensagens import (
    destruir_print_com_aviso,
    excluir_mensagem,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Helpers genéricos
# ─────────────────────────────────────────────


def _remover_medico(sessao: SessaoChamada, discord_id: int) -> bool:
    tamanho_antes = len(sessao.reconhecidos)
    sessao.reconhecidos = [m for m in sessao.reconhecidos if m.discord_id != discord_id]
    return len(sessao.reconhecidos) < tamanho_antes


def _construir_view_simples(
    titulo: str, linhas: str, cor: discord.Color
) -> discord.ui.LayoutView:
    layout = discord.ui.LayoutView(timeout=None)
    layout.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(f"# {titulo}"),
            discord.ui.TextDisplay(linhas),
            accent_color=cor,
        )
    )
    return layout


def _construir_blocos_texto(linhas: list[str], max_chars: int = 1500) -> list[str]:
    """Quebra uma lista de linhas em blocos que cabem num TextDisplay sem estourar
    o limite do Discord — evita o '... e mais N' truncando a lista real."""
    if not linhas:
        return ["_(nenhum)_"]
    blocos, atual, tamanho = [], [], 0
    for linha in linhas:
        if tamanho + len(linha) + 1 > max_chars and atual:
            blocos.append("\n".join(atual))
            atual, tamanho = [], 0
        atual.append(linha)
        tamanho += len(linha) + 1
    if atual:
        blocos.append("\n".join(atual))
    return blocos


def _construir_view_aguardando_print() -> discord.ui.LayoutView:
    return _construir_view_simples(
        "📸 Aguardando Print do /ems",
        "`•` Envie **neste canal** um print do comando `/ems`, o mais legível possível.\n"
        "`•` O sistema vai identificar os IDs FiveM automaticamente.\n"
        "`•` Você tem 5 minutos.",
        discord.Color.gold(),
    )


def _tem_cargo_bypass(discord_id: int, guild: discord.Guild) -> bool:
    membro = guild.get_member(discord_id)
    if membro is None:
        return False
    ids_bypass = {
        CARGOS[nome] for nome in CARGOS_BYPASS_PRESENCA_CHAMADA if nome in CARGOS
    }
    return any(cargo.id in ids_bypass for cargo in membro.roles)


def _construir_view_processando() -> discord.ui.LayoutView:
    return _construir_view_simples(
        "🔍 Processando Imagem",
        "Aguarde enquanto a imagem do `/ems` é analisada...",
        discord.Color.gold(),
    )


def _linha_medico(m: MedicoNaChamada) -> str:
    marcador = {"corrigido": "🔧", "manual": "➕"}.get(m.origem, "•")
    quem = f"<@{m.discord_id}>" if m.discord_id else (m.nome_discord or m.nome_ems)
    return f"{marcador} `{m.id_fivem}` — {quem}"


def _linha_desconhecido(e: dict) -> str:
    return f"❓ `{e['id_fivem']}` — {e['nome_ems']}"


# ─────────────────────────────────────────────
# Painel de Coordenação (inalterado, exceto imports já corretos)
# ─────────────────────────────────────────────


class PainelCoordenacaoView(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, membro: discord.Member, proximo_horario, liberado: bool):
        super().__init__(timeout=180)
        self.membro = membro

        if liberado:
            status_texto = "🟢 **Chamada liberada agora.**"
        else:
            timestamp = int(proximo_horario.timestamp())
            status_texto = (
                f"🔒 Próxima chamada liberada <t:{timestamp}:R> (<t:{timestamp}:t>)"
            )

        linhas = (
            f"`🩺` **Modo Coordenação ativo** — {membro.mention}\n"
            f"`📋` {status_texto}\n"
            "`ℹ️` Ao realizar a chamada, você vai enviar um print do `/ems` "
            "e o sistema vai cruzar com quem está de plantão."
        )

        componentes = [
            discord.ui.TextDisplay("# 🧭 Painel de Coordenação"),
            discord.ui.TextDisplay(linhas),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
        ]

        row = discord.ui.ActionRow()
        botao_chamada = discord.ui.Button(
            label="🩺 Realizar Chamada",
            style=discord.ButtonStyle.success,
            disabled=not liberado,
        )
        botao_chamada.callback = self._callback_realizar_chamada
        row.add_item(botao_chamada)

        botao_voltar = discord.ui.Button(
            label="↩️ Sair do Modo Coordenação", style=discord.ButtonStyle.secondary
        )
        botao_voltar.callback = self._callback_sair
        row.add_item(botao_voltar)

        componentes.append(row)
        self.container = discord.ui.Container(
            *componentes, accent_color=discord.Color.blurple()
        )
        self.add_item(self.container)

    @classmethod
    async def construir(cls, membro: discord.Member) -> "PainelCoordenacaoView":
        proximo_horario, liberado = await calcular_proximo_horario_permitido()
        return cls(membro, proximo_horario, liberado)

    async def _callback_sair(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        async with async_session() as session:
            resultado = await session.execute(
                select(EstadoPlantao).where(
                    EstadoPlantao.discord_id == interaction.user.id
                )
            )
            estado = resultado.scalar_one_or_none()
            if estado:
                estado.modo_coordenacao = False
                await session.commit()

        from src.plantao.plantao_panel import (
            InformacoesPlantaoView,
            _buscar_estado,
        )

        novo_estado = await _buscar_estado(interaction.user.id)
        nova_view = InformacoesPlantaoView(interaction.user, novo_estado)
        await interaction.edit_original_response(view=nova_view)

    async def _callback_realizar_chamada(self, interaction: discord.Interaction):
        if not membro_e_doutor_ou_acima(interaction.user):
            await interaction.response.send_message(
                "❌ Apenas Doutor ou acima pode realizar chamadas.", ephemeral=True
            )
            return

        conseguiu, outro_doutor_id = await tentar_iniciar_chamada(interaction.user.id)
        if not conseguiu:
            await interaction.response.send_message(
                f"⏳ <@{outro_doutor_id}> já está realizando uma chamada agora. Aguarde o próximo período.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        async with async_session() as session:
            registro_chamada = Chamada(doutor_id=interaction.user.id)
            session.add(registro_chamada)
            await session.commit()
            chamada_id = registro_chamada.id

        sessao = SessaoChamada(
            doutor_id=interaction.user.id,
            chamada_id=chamada_id,
            canal_id=interaction.channel_id,
        )
        definir_sessao(sessao)

        await interaction.edit_original_response(
            view=_construir_view_aguardando_print()
        )  # 👈 edita, não envia novo

        bot = interaction.client

        def checagem(msg: discord.Message) -> bool:
            return (
                msg.author.id == interaction.user.id
                and msg.channel.id == interaction.channel_id
                and len(msg.attachments) > 0
            )

        try:
            mensagem_print = await bot.wait_for("message", timeout=300, check=checagem)
        except TimeoutError:
            await finalizar_chamada(marcar_ultima_chamada=False)
            definir_sessao(None)
            await interaction.edit_original_response(
                view=_construir_view_simples(
                    "⏱️ Tempo Esgotado",
                    "Nenhum print recebido a tempo. Chamada cancelada.",
                    discord.Color.red(),
                )
            )
            return

        # dentro de _callback_realizar_chamada, remove o asyncio.create_task daqui:
        anexo = mensagem_print.attachments[0]
        sessao.print_ems_url = anexo.url
        sessao.print_ems_mensagem = (
            mensagem_print  # 👈 só guarda, não agenda exclusão ainda
        )
        await _processar_print_ems(interaction, anexo.url)


# ─────────────────────────────────────────────
# Processamento do OCR + cruzamento
# ─────────────────────────────────────────────


async def _processar_print_ems(interaction: discord.Interaction, url_imagem: str):
    sessao = obter_sessao()
    guild = interaction.guild

    await interaction.edit_original_response(
        view=_construir_view_processando()
    )  # 👈 edita

    try:
        resultado = await asyncio.wait_for(
            extrair_medicos_do_print_ems(url_imagem), timeout=90
        )
    except asyncio.TimeoutError:
        await finalizar_chamada(marcar_ultima_chamada=False)
        await destruir_print_com_aviso(sessao.print_ems_mensagem, delay=10)
        definir_sessao(None)
        await interaction.edit_original_response(
            view=_construir_view_simples(
                "❌ Chamada Cancelada",
                "Processamento excedeu o tempo limite.",
                discord.Color.red(),
            )
        )
        return
    except OcrEmsError as exc:
        await finalizar_chamada(marcar_ultima_chamada=False)
        await destruir_print_com_aviso(sessao.print_ems_mensagem, delay=10)
        definir_sessao(None)
        await interaction.edit_original_response(
            view=_construir_view_simples(
                "❌ Chamada Cancelada",
                f"Erro ao ler a imagem: {exc}",
                discord.Color.red(),
            )
        )
        return
    except Exception:
        logger.exception("💥 Falha inesperada no OCR")
        await finalizar_chamada(marcar_ultima_chamada=False)
        await destruir_print_com_aviso(sessao.print_ems_mensagem, delay=10)
        definir_sessao(None)
        await interaction.edit_original_response(
            view=_construir_view_simples(
                "❌ Chamada Cancelada",
                "Erro inesperado ao processar a imagem.",
                discord.Color.red(),
            )
        )
        return

    medicos_ems = resultado["medicos"]
    sessao.total_medicos_ems = len(medicos_ems)

    ids_no_ems, reconhecidos, nao_reconhecidos = set(), [], []

    async with async_session() as session:
        resultado_db = await session.execute(
            select(
                Recrutamento.id_fivem,
                Recrutamento.discord_id_candidato,
                Usuario.nickname_atual,
            )
            .join(Usuario, Usuario.discord_id == Recrutamento.discord_id_candidato)
            .where(
                Recrutamento.status == "APROVADO", Recrutamento.id_fivem.is_not(None)
            )
            .order_by(Recrutamento.id.asc())
        )
        aprovados_por_id = {
            row.id_fivem: MembroConhecido(
                id_fivem=row.id_fivem,
                nome=row.nickname_atual or "",
                discord_id=row.discord_id_candidato,
            )
            for row in resultado_db.all()
        }

        membros_via_apelido = construir_membros_via_apelido(
            guild
        )  # já filtrado por prefixo
        membros_conhecidos = combinar_membros(
            list(aprovados_por_id.values()), membros_via_apelido
        )
        sessao.membros_conhecidos = membros_conhecidos

        resultado_toggle = await session.execute(
            select(EstadoPlantao).where(EstadoPlantao.toggle_ligado.is_(True))
        )
        estados_ligados = resultado_toggle.scalars().all()

    validados = validar_medicos(medicos_ems, membros_conhecidos)

    for v in validados:
        id_final = v.id_corrigido or v.id_lido
        ids_no_ems.add(id_final)
        if v.status in ("confirmado", "corrigido"):
            membro_db = guild.get_member(v.membro.discord_id)
            reconhecidos.append(
                MedicoNaChamada(
                    id_fivem=v.membro.id_fivem,
                    discord_id=v.membro.discord_id,
                    nome_ems=v.nome_lido,
                    nome_discord=membro_db.display_name if membro_db else None,
                    confianca=1.0 if v.status == "confirmado" else 0.7,
                    origem="ocr" if v.status == "confirmado" else "corrigido",
                    motivo=v.motivo,
                )
            )
        else:
            nao_reconhecidos.append({"id_fivem": v.id_lido, "nome_ems": v.nome_lido})

    sessao.reconhecidos = reconhecidos
    sessao.nao_reconhecidos = nao_reconhecidos
    sessao.total_toggle_ligado = len(estados_ligados)

    ids_fivem_com_toggle = {
        e.id_fivem: e.discord_id for e in estados_ligados if e.id_fivem
    }
    ids_com_toggle_fora_do_ems = set(ids_fivem_com_toggle.keys()) - ids_no_ems
    sessao.toggle_ligado_mas_nao_no_ems = [
        MedicoNaChamada(
            id_fivem=id_fivem, discord_id=ids_fivem_com_toggle[id_fivem], nome_ems="—"
        )
        for id_fivem in ids_com_toggle_fora_do_ems
    ]

    await _processar_ausentes_do_ems(interaction, sessao)

    view_etapa_1 = _construir_etapa_1(
        sessao, guild
    )  # retirado await pois nao é mais async def
    await interaction.edit_original_response(view=view_etapa_1)


async def _processar_ausentes_do_ems(
    interaction: discord.Interaction, sessao: SessaoChamada
):
    guild = interaction.guild
    for medico in sessao.toggle_ligado_mas_nao_no_ems:
        membro = guild.get_member(medico.discord_id)
        if membro is None:
            continue
        try:
            await membro.send(
                "⚠️ Durante a chamada de auditoria, você estava com o plantão ativo no Discord "
                "mas não foi encontrado no `/ems` da cidade. Seu plantão foi encerrado automaticamente."
            )
        except discord.Forbidden:
            pass
        await desligar_servico(membro)
        await registrar_falta(
            membro.id,
            sessao.chamada_id,
            "Toggle ligado no Discord, ausente no /ems",
            guild,
        )


def _deduplicar_reconhecidos(sessao: SessaoChamada):
    vistos = set()
    unicos = []
    for m in sessao.reconhecidos:
        chave = m.discord_id if m.discord_id else m.id_fivem
        if chave in vistos:
            continue
        vistos.add(chave)
        unicos.append(m)
    sessao.reconhecidos = unicos


# ─────────────────────────────────────────────
# ETAPA 1 — Verificação
# ─────────────────────────────────────────────


def _construir_etapa_1(
    sessao: SessaoChamada, guild: discord.Guild
) -> discord.ui.LayoutView:
    sessao.etapa_atual = 1
    _deduplicar_reconhecidos(sessao)

    corrigidos = [m for m in sessao.reconhecidos if m.origem == "corrigido"]
    demais = [m for m in sessao.reconhecidos if m.origem != "corrigido"]

    blocos_demais = _construir_blocos_texto([_linha_medico(m) for m in demais])

    resumo = (
        f"`📋` Total no `/ems`: **{sessao.total_medicos_ems}**\n"
        f"`✅` Identificados: **{len(sessao.reconhecidos)}**\n"
        f"`❓` Ainda não identificados: **{len(sessao.nao_reconhecidos)}**\n\n"
        "Confira a lista. Faltando alguém que está no `/ems`? Adicione manualmente.\n"
        "Alguém errado na lista? Remova pelo botão ou pelo bloco de correções abaixo."
    )

    componentes = [
        discord.ui.TextDisplay("# ⏳ Etapa 1 — Verificação"),
        discord.ui.TextDisplay(resumo),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
    ]

    # Bloco de destaque: correções automáticas, maior risco de falso positivo
    if corrigidos:
        linhas_corrigidos = "\n".join(
            f"🔧 `{m.id_fivem}` — <@{m.discord_id}>\n> _{m.motivo or 'correção automática'}_"
            for m in corrigidos
        )
        componentes.append(
            discord.ui.TextDisplay(
                f"**⚠️ Correções automáticas — confira com atenção**\n{linhas_corrigidos}"
            )
        )

        if len(corrigidos) <= 25:
            select_remover_corrigidos = discord.ui.Select(
                placeholder="Selecione quem está ERRADO aqui pra remover",
                options=[
                    discord.SelectOption(
                        label=f"{m.nome_discord or m.nome_ems} | {m.id_fivem}",
                        value=str(m.discord_id),
                    )
                    for m in corrigidos
                ],
                min_values=0,
                max_values=len(corrigidos),
            )
            select_remover_corrigidos.callback = _callback_remover_corrigidos
            row_corrigidos = discord.ui.ActionRow()
            row_corrigidos.add_item(select_remover_corrigidos)
            componentes.append(row_corrigidos)

        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

    # Lista completa dos demais (confirmados direto + manuais)
    componentes.append(discord.ui.TextDisplay("**✅ Confirmados / Adicionados**"))
    for bloco in blocos_demais:
        componentes.append(discord.ui.TextDisplay(bloco))
    componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

    # Busca pra adicionar
    row_select = discord.ui.ActionRow()
    user_select = discord.ui.UserSelect(
        placeholder="🔎 Adicionar por menção (selecione o usuário)"
    )
    user_select.callback = _callback_userselect_adicionar
    row_select.add_item(user_select)
    componentes.append(row_select)

    row_botoes = discord.ui.ActionRow()
    botao_discord_id = discord.ui.Button(
        label="🆔 Buscar por Discord ID", style=discord.ButtonStyle.secondary
    )
    botao_discord_id.callback = _callback_abrir_modal_discord_id
    row_botoes.add_item(botao_discord_id)

    botao_fivem = discord.ui.Button(
        label="🎮 Buscar por ID FiveM", style=discord.ButtonStyle.secondary
    )
    botao_fivem.callback = _callback_abrir_modal_id_fivem
    row_botoes.add_item(botao_fivem)

    botao_remover = discord.ui.Button(
        label="❌ Remover", style=discord.ButtonStyle.danger
    )
    botao_remover.callback = _callback_abrir_modal_remover
    row_botoes.add_item(botao_remover)
    componentes.append(row_botoes)

    row_continuar = discord.ui.ActionRow()
    botao_continuar = discord.ui.Button(
        label="➡️ Continuar", style=discord.ButtonStyle.primary
    )
    botao_continuar.callback = _callback_ir_etapa_2
    row_continuar.add_item(botao_continuar)
    componentes.append(row_continuar)

    layout = discord.ui.LayoutView(timeout=600)
    layout.add_item(
        discord.ui.Container(*componentes, accent_color=discord.Color.blurple())
    )
    return layout


def _resolver_id_fivem_do_membro(
    sessao: SessaoChamada, discord_id: int, membro: discord.Member
) -> str | None:
    """Tenta achar o id_fivem de um membro específico: primeiro no cache de
    membros_conhecidos (Recrutamento/apelido), senão tenta extrair do apelido atual."""
    for m in sessao.membros_conhecidos:
        if m.discord_id == discord_id:
            return m.id_fivem
    from src.plantao.ocr.scraping_membros import extrair_id_do_apelido

    nome_exibido = membro.nick or membro.display_name or membro.name
    return extrair_id_do_apelido(nome_exibido)


def _adicionar_medico_manual(
    sessao: SessaoChamada, guild: discord.Guild, discord_id: int, id_fivem: str | None
) -> bool:
    """Retorna True se adicionou de fato, False se a pessoa já estava na lista (evita duplicata)."""
    ja_existe = any(m.discord_id == discord_id for m in sessao.reconhecidos)
    if ja_existe:
        return False

    membro = guild.get_member(discord_id)
    if id_fivem is None and membro:
        id_fivem = _resolver_id_fivem_do_membro(sessao, discord_id, membro)

    novo = MedicoNaChamada(
        id_fivem=id_fivem or "N/A",
        discord_id=discord_id,
        nome_ems="(adicionado manualmente)",
        nome_discord=membro.display_name if membro else None,
        origem="manual",
    )
    sessao.reconhecidos.append(novo)

    if id_fivem:
        sessao.nao_reconhecidos = [
            e for e in sessao.nao_reconhecidos if e["id_fivem"] != id_fivem
        ]

    return True


async def _callback_userselect_adicionar(interaction: discord.Interaction):
    sessao = obter_sessao()
    if sessao is None:
        await interaction.response.send_message("❌ Sessão expirada.", ephemeral=True)
        return

    membro_selecionado = interaction.data["values"][0]
    discord_id = int(membro_selecionado)

    _adicionar_medico_manual(sessao, interaction.guild, discord_id, None)

    await interaction.response.defer(ephemeral=True)
    await interaction.edit_original_response(
        view=_construir_etapa_1(sessao, interaction.guild)
    )


class ModalBuscarPorDiscordId(discord.ui.Modal, title="Buscar por Discord ID"):
    discord_id_input = discord.ui.TextInput(
        label="Discord ID", placeholder="Ex: 859100649366356000", required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        sessao = obter_sessao()
        if sessao is None:
            await interaction.response.send_message(
                "❌ Sessão expirada.", ephemeral=True
            )
            return

        valor = self.discord_id_input.value.strip()
        if not valor.isdigit():
            await interaction.response.send_message(
                "❌ Discord ID inválido.", ephemeral=True
            )
            return

        discord_id = int(valor)
        membro = interaction.guild.get_member(discord_id)
        if membro is None:
            await interaction.response.send_message(
                "❌ Membro não encontrado neste servidor.", ephemeral=True
            )
            return

        _adicionar_medico_manual(sessao, interaction.guild, discord_id, None)
        await interaction.response.defer(ephemeral=True)
        await interaction.edit_original_response(
            view=_construir_etapa_1(sessao, interaction.guild)
        )


class ModalBuscarPorIdFivem(discord.ui.Modal, title="Buscar por ID FiveM"):
    id_fivem_input = discord.ui.TextInput(
        label="ID FiveM", placeholder="Ex: 054623", required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        sessao = obter_sessao()
        if sessao is None:
            await interaction.response.send_message(
                "❌ Sessão expirada.", ephemeral=True
            )
            return

        id_fivem = self.id_fivem_input.value.strip()
        membro_conhecido = next(
            (m for m in sessao.membros_conhecidos if m.id_fivem == id_fivem), None
        )

        if membro_conhecido is None:
            await interaction.response.send_message(
                "❌ Nenhum membro no servidor com esse ID FiveM (nem no Recrutamento, nem no apelido).",
                ephemeral=True,
            )
            return

        _adicionar_medico_manual(
            sessao, interaction.guild, membro_conhecido.discord_id, id_fivem
        )
        await interaction.response.defer(ephemeral=True)
        await interaction.edit_original_response(
            view=_construir_etapa_1(sessao, interaction.guild)
        )


class ModalRemoverMedico(discord.ui.Modal, title="Remover da Lista"):
    identificador = discord.ui.TextInput(
        label="ID FiveM, Discord ID ou Menção",
        placeholder="Ex: 054623 ou @membro ou 859100649366356000",
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        sessao = obter_sessao()
        if sessao is None:
            await interaction.response.send_message(
                "❌ Sessão expirada.", ephemeral=True
            )
            return

        valor = (
            self.identificador.value.strip()
            .replace("<@", "")
            .replace(">", "")
            .replace("!", "")
        )

        alvo = None
        if valor.isdigit() and len(valor) >= 15:
            alvo = next(
                (m for m in sessao.reconhecidos if m.discord_id == int(valor)), None
            )
        elif valor.isdigit():
            alvo = next((m for m in sessao.reconhecidos if m.id_fivem == valor), None)

        if alvo is None:
            await interaction.response.send_message(
                "❌ Ninguém na lista atual bate com esse identificador.",
                ephemeral=True,
            )
            return

        _remover_medico(sessao, alvo.discord_id)
        await interaction.response.defer(ephemeral=True)
        await interaction.edit_original_response(
            view=_construir_etapa_1(sessao, interaction.guild)
        )


async def _callback_abrir_modal_remover(interaction: discord.Interaction):
    await interaction.response.send_modal(ModalRemoverMedico())


async def _callback_abrir_modal_discord_id(interaction: discord.Interaction):
    await interaction.response.send_modal(ModalBuscarPorDiscordId())


async def _callback_abrir_modal_id_fivem(interaction: discord.Interaction):
    await interaction.response.send_modal(ModalBuscarPorIdFivem())


async def _callback_ir_etapa_2(interaction: discord.Interaction):
    sessao = obter_sessao()
    await interaction.response.defer(ephemeral=True)
    await interaction.edit_original_response(
        view=_construir_etapa_2(sessao, interaction.guild)
    )


async def _callback_remover_corrigidos(interaction: discord.Interaction):
    ids_selecionados = {int(v) for v in interaction.data["values"]}
    sessao = obter_sessao()

    for discord_id in ids_selecionados:
        _remover_medico(sessao, discord_id)

    await interaction.response.defer(ephemeral=True)
    await interaction.edit_original_response(
        view=_construir_etapa_1(sessao, interaction.guild)
    )


# ─────────────────────────────────────────────
# ETAPA 2 — Desconhecidos
# ─────────────────────────────────────────────


def _construir_etapa_2(
    sessao: SessaoChamada, guild: discord.Guild
) -> discord.ui.LayoutView:
    sessao.etapa_atual = 2
    linhas = [_linha_desconhecido(e) for e in sessao.nao_reconhecidos]
    blocos = _construir_blocos_texto(linhas)

    resumo = (
        f"`❓` Restam **{len(sessao.nao_reconhecidos)}** não identificados.\n"
        "Se não forem médicos do nosso hospital, marque como Hospital Norte pra liberar a próxima etapa."
    )

    componentes = [
        discord.ui.TextDisplay("# ⌛ Etapa 2 — Desconhecidos"),
        discord.ui.TextDisplay(resumo),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
    ]
    for bloco in blocos:
        componentes.append(discord.ui.TextDisplay(bloco))
    componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

    row = discord.ui.ActionRow()
    botao_voltar = discord.ui.Button(
        label="⬅️ Voltar", style=discord.ButtonStyle.secondary
    )
    botao_voltar.callback = lambda i: _voltar_para_etapa(i, 1)
    row.add_item(botao_voltar)

    if sessao.nao_reconhecidos:
        botao_norte = discord.ui.Button(
            label="🏥 Médico CMN (Hospital Norte)", style=discord.ButtonStyle.secondary
        )
        botao_norte.callback = _callback_marcar_como_norte
        row.add_item(botao_norte)

    botao_continuar = discord.ui.Button(
        label="➡️ Continuar",
        style=discord.ButtonStyle.primary,
        disabled=bool(sessao.nao_reconhecidos),
    )
    botao_continuar.callback = _callback_ir_etapa_3
    row.add_item(botao_continuar)
    componentes.append(row)

    layout = discord.ui.LayoutView(timeout=600)
    layout.add_item(
        discord.ui.Container(*componentes, accent_color=discord.Color.blurple())
    )
    return layout


async def _callback_marcar_como_norte(interaction: discord.Interaction):
    sessao = obter_sessao()
    sessao.medicos_norte.extend(sessao.nao_reconhecidos)
    sessao.nao_reconhecidos = []
    await interaction.response.defer(ephemeral=True)
    await interaction.edit_original_response(
        view=_construir_etapa_2(sessao, interaction.guild)
    )


async def _voltar_para_etapa(interaction: discord.Interaction, etapa: int):
    sessao = obter_sessao()
    await interaction.response.defer(ephemeral=True)
    construtor = {1: _construir_etapa_1, 2: _construir_etapa_2, 3: _construir_etapa_3}[
        etapa
    ]
    await interaction.edit_original_response(view=construtor(sessao, interaction.guild))


async def _callback_ir_etapa_3(interaction: discord.Interaction):
    sessao = obter_sessao()
    await interaction.response.defer(ephemeral=True)
    await interaction.edit_original_response(
        view=_construir_etapa_3(sessao, interaction.guild)
    )


# ─────────────────────────────────────────────
# ETAPA 3 — Em Serviço (confirmação de presença)
# ─────────────────────────────────────────────


def _construir_etapa_3(
    sessao: SessaoChamada, guild: discord.Guild
) -> discord.ui.LayoutView:
    sessao.etapa_atual = 3

    sessao.bypass_presenca = [
        m for m in sessao.reconhecidos if _tem_cargo_bypass(m.discord_id, guild)
    ]
    # 👇 MUDANÇA: usa TODOS os identificados na Etapa 1 (já excluindo quem foi
    # movido pra Hospital Norte, já que esses nunca entraram em `reconhecidos`)
    sessao.presentes_no_ems_toggle_ligado = [
        m
        for m in sessao.reconhecidos
        if m.discord_id not in {b.discord_id for b in sessao.bypass_presenca}
    ]

    linha_bypass = (
        f"`🛡️` **{len(sessao.bypass_presenca)}** já contam como presentes automaticamente (cargo com dispensa).\n"
        if sessao.bypass_presenca
        else ""
    )

    resumo = (
        f"{linha_bypass}"
        f"`🟢` **{len(sessao.presentes_no_ems_toggle_ligado)}** aguardando confirmação de presença.\n"
        "Todos começam marcados como **presentes**. Verifique call, rádio in-game ou chame na interna "
        "do hospital — **desmarque** quem não responder (ficará com falta)."
    )

    componentes = [
        discord.ui.TextDisplay("# ⌛ Etapa 3 — Em Serviço"),
        discord.ui.TextDisplay(resumo),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
    ]

    row_botoes = discord.ui.ActionRow()
    botao_voltar = discord.ui.Button(
        label="⬅️ Voltar", style=discord.ButtonStyle.secondary
    )
    botao_voltar.callback = lambda i: _voltar_para_etapa(i, 2)
    row_botoes.add_item(botao_voltar)

    if not sessao.presentes_no_ems_toggle_ligado:
        botao_continuar = discord.ui.Button(
            label="➡️ Continuar", style=discord.ButtonStyle.primary
        )
        botao_continuar.callback = _callback_ir_etapa_4
        row_botoes.add_item(botao_continuar)
        componentes.append(row_botoes)

        layout = discord.ui.LayoutView(timeout=600)
        layout.add_item(
            discord.ui.Container(*componentes, accent_color=discord.Color.blurple())
        )
        return layout

    # Se passar de 25, precisa paginar — por ora, mostra até 25 no select e
    # avisa no texto que o restante fica marcado como presente por padrão
    lista_para_select = sessao.presentes_no_ems_toggle_ligado[:25]
    if len(sessao.presentes_no_ems_toggle_ligado) > 25:
        componentes.append(
            discord.ui.TextDisplay(
                f"⚠️ **Atenção:** {len(sessao.presentes_no_ems_toggle_ligado)} médicos identificados, "
                f"mas o menu só mostra os primeiros 25. Os demais permanecem como presentes por padrão."
            )
        )

    opcoes = [
        discord.SelectOption(
            label=f"{m.nome_discord or m.nome_ems} | {m.id_fivem}",
            value=str(m.discord_id),
            default=m.discord_id not in sessao.faltantes_ids,
        )
        for m in lista_para_select
    ]

    select_presenca = discord.ui.Select(
        placeholder="Todos marcados = presentes. Desmarque quem não respondeu.",
        options=opcoes,
        min_values=0,
        max_values=len(opcoes),
    )
    select_presenca.callback = _callback_atualizar_faltantes
    row_select = discord.ui.ActionRow()
    row_select.add_item(select_presenca)
    componentes.append(row_select)

    botao_continuar = discord.ui.Button(
        label="➡️ Continuar", style=discord.ButtonStyle.primary
    )
    botao_continuar.callback = _callback_ir_etapa_4
    row_botoes.add_item(botao_continuar)
    componentes.append(row_botoes)

    layout = discord.ui.LayoutView(timeout=600)
    layout.add_item(
        discord.ui.Container(*componentes, accent_color=discord.Color.blurple())
    )
    return layout


async def _callback_atualizar_faltantes(interaction: discord.Interaction):
    sessao = obter_sessao()
    ids_selecionados = {
        int(v) for v in interaction.data["values"]
    }  # quem ficou marcado = presente
    todos_ids = {m.discord_id for m in sessao.presentes_no_ems_toggle_ligado}
    sessao.faltantes_ids = todos_ids - ids_selecionados  # quem foi desmarcado = falta

    await interaction.response.defer(ephemeral=True)
    await interaction.edit_original_response(
        view=_construir_etapa_3(sessao, interaction.guild)
    )


async def _callback_ir_etapa_4(interaction: discord.Interaction):
    sessao = obter_sessao()
    await interaction.response.defer(ephemeral=True)
    await interaction.edit_original_response(
        view=_construir_etapa_4(sessao, interaction.guild)
    )


# ─────────────────────────────────────────────
# ETAPA 4 — Conclusão
# ─────────────────────────────────────────────


def _construir_etapa_4(
    sessao: SessaoChamada, guild: discord.Guild
) -> discord.ui.LayoutView:
    sessao.etapa_atual = 4

    presentes = sessao.bypass_presenca + [
        m
        for m in sessao.presentes_no_ems_toggle_ligado
        if m.discord_id not in sessao.faltantes_ids
    ]
    faltantes = [
        m
        for m in sessao.presentes_no_ems_toggle_ligado
        if m.discord_id in sessao.faltantes_ids
    ]

    linhas_presentes = "\n".join(_linha_medico(m) for m in presentes) or "_(nenhum)_"
    linhas_faltantes = "\n".join(_linha_medico(m) for m in faltantes) or "_(nenhum)_"
    linhas_norte = (
        "\n".join(_linha_desconhecido(e) for e in sessao.medicos_norte) or "_(nenhum)_"
    )

    resumo = (
        f"`✅` Presentes: **{len(presentes)}**\n"
        f"`❌` Faltas: **{len(faltantes)}**\n"
        f"`🏥` Hospital Norte: **{len(sessao.medicos_norte)}**\n\n"
        "Revise antes de finalizar — voltar ainda é possível."
    )

    componentes = [
        discord.ui.TextDisplay("# ✅ Etapa 4 — Conclusão"),
        discord.ui.TextDisplay(resumo),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
        discord.ui.TextDisplay(f"**✅ Presentes**\n{linhas_presentes}"),
        discord.ui.TextDisplay(f"**❌ Faltas**\n{linhas_faltantes}"),
        discord.ui.TextDisplay(f"**🏥 Hospital Norte**\n{linhas_norte}"),
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
    ]

    row = discord.ui.ActionRow()
    botao_voltar = discord.ui.Button(
        label="⬅️ Voltar", style=discord.ButtonStyle.secondary
    )
    botao_voltar.callback = lambda i: _voltar_para_etapa(i, 3)
    row.add_item(botao_voltar)

    botao_finalizar = discord.ui.Button(
        label="✅ Finalizar Chamada", style=discord.ButtonStyle.success
    )
    botao_finalizar.callback = _callback_finalizar_chamada
    row.add_item(botao_finalizar)
    componentes.append(row)

    layout = discord.ui.LayoutView(timeout=600)
    layout.add_item(
        discord.ui.Container(*componentes, accent_color=discord.Color.blurple())
    )
    return layout


async def _callback_finalizar_chamada(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    sessao = obter_sessao()
    guild = interaction.guild

    presentes = sessao.bypass_presenca + [
        m
        for m in sessao.presentes_no_ems_toggle_ligado
        if m.discord_id not in sessao.faltantes_ids
    ]
    faltantes = [
        m
        for m in sessao.presentes_no_ems_toggle_ligado
        if m.discord_id in sessao.faltantes_ids
    ]

    for medico in faltantes:
        membro = guild.get_member(medico.discord_id)
        if membro is None:
            continue
        await registrar_falta(
            membro.id, sessao.chamada_id, "Não respondeu à chamada (call/rádio)", guild
        )
        await desligar_servico(membro)
        try:
            await membro.send(
                "🔴 Você foi desconectado e seu plantão encerrado por não responder à chamada de auditoria."
            )
        except discord.Forbidden:
            pass

    for medico in presentes:
        async with async_session() as session:
            resultado = await session.execute(
                select(EstadoPlantao).where(
                    EstadoPlantao.discord_id == medico.discord_id
                )
            )
            estado = resultado.scalar_one_or_none()
            if estado:
                estado.saldo_moedas += 1
            await session.commit()

    async with async_session() as session:
        resultado = await session.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == sessao.doutor_id)
        )
        estado_doutor = resultado.scalar_one_or_none()
        if estado_doutor:
            estado_doutor.saldo_moedas += 1
            await session.commit()

        resultado_chamada = await session.execute(
            select(Chamada).where(Chamada.id == sessao.chamada_id)
        )
        registro_chamada = resultado_chamada.scalar_one_or_none()
        if registro_chamada:
            registro_chamada.total_medicos_ems = sessao.total_medicos_ems
            registro_chamada.total_toggle_ligado = sessao.total_toggle_ligado
            registro_chamada.total_presentes = len(presentes)
            registro_chamada.total_ausentes = len(faltantes) + len(
                sessao.toggle_ligado_mas_nao_no_ems
            )
            await session.commit()

    await _enviar_log_chamada_canal(guild, sessao, presentes, faltantes)

    if sessao.print_ems_mensagem is not None:
        asyncio.create_task(
            excluir_mensagem(sessao.print_ems_mensagem, delay=60)
        )  # limpeza silenciosa, sucesso

    await finalizar_chamada(marcar_ultima_chamada=True)

    await interaction.edit_original_response(
        view=_construir_view_simples(
            "✅ Chamada Finalizada",
            "Registrada com sucesso no canal de logs.",
            discord.Color.green(),
        )
    )

    definir_sessao(None)


async def _enviar_log_chamada_canal(
    guild: discord.Guild,
    sessao: SessaoChamada,
    presentes: list[MedicoNaChamada],
    faltantes: list[MedicoNaChamada],
):
    canal_log = guild.get_channel(CANAIS.get("LOG_CHAMADAS"))
    if canal_log is None:
        return

    linhas_presentes = (
        "\n".join(f"`✅` `{m.id_fivem}` — <@{m.discord_id}>" for m in presentes)
        or "_(nenhum)_"
    )
    linhas_faltantes = (
        "\n".join(f"`❌` `{m.id_fivem}` — <@{m.discord_id}>" for m in faltantes)
        or "_(nenhum)_"
    )
    linhas_norte = (
        "\n".join(
            f"`❓` `{e['id_fivem']}` — {e['nome_ems']}" for e in sessao.medicos_norte
        )
        or "_(nenhum)_"
    )

    cabecalho_stats = (
        f"`📋` **Total no** `/ems`: {sessao.total_medicos_ems}\n"
        f"`✅` **Identificados (Hospital Sul):** {len(sessao.reconhecidos)}\n"
        f"`🟢` **Elegíveis p/ confirmar presença:** {len(sessao.presentes_no_ems_toggle_ligado) + len(sessao.bypass_presenca)}\n"
        f"`❓` **Não identificados (Norte/Desconhecido):** {len(sessao.medicos_norte)}\n"
        f"`⚠️` **Toggle ligado mas ausente do EMS (já processado):** {len(sessao.toggle_ligado_mas_nao_no_ems)}\n"
        f"`👨‍⚕️` **Responsável pela chamada:** <@{sessao.doutor_id}>"
    )

    icon_url = guild.icon.url if guild.icon else None
    agora = int(datetime.now(timezone.utc).timestamp())

    componentes = []

    texto_titulo_stats = f"# 📋 Chamada de Plantão Realizada\n{cabecalho_stats}"
    if icon_url:
        componentes.append(
            discord.ui.Section(
                texto_titulo_stats, accessory=discord.ui.Thumbnail(icon_url)
            )
        )
    else:
        componentes.append(discord.ui.TextDisplay(texto_titulo_stats))

    componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

    componentes.append(
        discord.ui.TextDisplay(f"**Médicos presentes:**\n{linhas_presentes}")
    )
    componentes.append(
        discord.ui.TextDisplay(f"**Médicos com Falta/Ausência:**\n{linhas_faltantes}")
    )
    componentes.append(
        discord.ui.TextDisplay(f"**Identificados — Hospital Norte**\n{linhas_norte}")
    )

    if sessao.print_ems_url:
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        componentes.append(
            discord.ui.MediaGallery(discord.MediaGalleryItem(sessao.print_ems_url))
        )

    componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
    componentes.append(discord.ui.TextDisplay(f"-# {guild.name} • <t:{agora}:f>"))

    layout = discord.ui.LayoutView(timeout=None)
    layout.add_item(
        discord.ui.Container(*componentes, accent_color=discord.Color.blurple())
    )
    await canal_log.send(view=layout)
