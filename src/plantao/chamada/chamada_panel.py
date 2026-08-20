"""
Todos os cards da chamada de plantao, do inicio ao fechamento.

Este e o arquivo mais longo do dominio, porque a chamada tem muitos passos:
abrir a sessao, ler o print do EMS, conferir os ids que o OCR reconheceu,
corrigir na mao quem ficou de fora e fechar a sessao.

Onde as coisas moram
--------------------
- A sessao em andamento fica em chamada_state.py, nao aqui.
- As gravacoes no banco ficam em chamada_service.py.
- A leitura da imagem fica em ocr/.
Este arquivo e a cara da chamada: ele monta os cards e chama esses tres.
"""

import asyncio
import io
import logging
import os
from datetime import (
    datetime,
    timezone,
)

import aiohttp
import discord
from sqlalchemy import select

from src.config import (
    CANAIS,
    CARGOS,
    CARGOS_BYPASS_PRESENCA_CHAMADA,
    TEMPO_MAXIMO_SESSAO_CHAMADA_MINUTOS,
    TIMEOUT_INTERACAO_POS_OCR_SEGUNDOS,
    TIMEOUT_PRINT_EMS_SEGUNDOS,
)
from src.database.conexao import async_session
from src.database.models import (
    Chamada,
    EstadoPlantao,
    Recrutamento,
    Usuario,
)
from src.plantao.chamada.chamada_service import (
    MOTIVO_CANCEL_ERRO,
    MOTIVO_CANCEL_TIMEOUT_INTERACAO,
    MOTIVO_CANCEL_TIMEOUT_PRINT,
    calcular_proximo_horario_permitido,
    cancelar_chamada,
    finalizar_chamada,
    liberar_lock_se_expirado,
    registrar_falta,
    tentar_iniciar_chamada,
)
from src.plantao.chamada.chamada_state import (
    MedicoNaChamada,
    SessaoChamada,
    definir_sessao,
    obter_sessao,
)
from src.plantao.chamada.ocr.leitura_de_membros_service import (
    combinar_membros,
    construir_membros_via_apelido,
    extrair_id_do_apelido,
)
from src.plantao.chamada.ocr.ocr_ems_service import (
    OcrEmsError,
    extrair_medicos_do_print_ems,
)
from src.plantao.chamada.validacao_ids_service import (
    MembroConhecido,
    nomes_ou_ids_batem_com_reconhecido,
    nomes_parecidos,
    normalizar_nome,
    validar_medicos,
)
from src.plantao.plantao_permissoes import e_diretoria
from src.plantao.plantao_service import (
    desligar_servico,
    membro_e_doutor_ou_acima,
)
from src.utils.error_handling import (
    LoggingViewMixin,
    ignorar_falha_cosmetica,
)
from src.utils.mensagens import (
    destruir_print_com_aviso,
    editar_mensagem_original,
    excluir_mensagem,
    responder_aviso,
    responder_erro,
    responder_info,
)

registrador = logging.getLogger(__name__)

logger = logging.getLogger(__name__)


class _ViewSessaoChamada(discord.ui.LayoutView):
    """LayoutView da sessão: ao expirar sem interação, cancela sem cooldown."""

    def __init__(self, *, timeout: float | None = None):
        if timeout is None:
            timeout = float(TIMEOUT_INTERACAO_POS_OCR_SEGUNDOS)
        super().__init__(timeout=timeout)

    async def on_timeout(self):
        """Cancela uma sessão abandonada para não manter a trava ocupada.

        Limpa tanto o controle persistido quanto a sessão em memória, sem
        registrar cooldown. Assim outro doutor consegue recomeçar depois do
        tempo configurado em TIMEOUT_INTERACAO_POS_OCR_SEGUNDOS, em vez de o
        sistema parecer ocupado por uma chamada esquecida.
        """
        try:
            sessao = obter_sessao()
            if sessao is None:
                return
            await cancelar_chamada(motivo=MOTIVO_CANCEL_TIMEOUT_INTERACAO)
            definir_sessao(None)
            registrador.info(
                "[chamada] view expirou — chamada cancelada (sem interação)"
            )
        except Exception as erro:
            registrador.error(f"[chamada] on_timeout falhou: {erro}")


# ─────────────────────────────────────────────
# Helpers genéricos
# ─────────────────────────────────────────────


def _remover_medico(sessao: SessaoChamada, discord_id: int) -> bool:
    """Tira o médico dos identificados e devolve a linha do EMS aos não identificados.

    Sem isso, uma correção automática errada que o doutor remove some do fluxo:
    o ID não volta pra Etapa 2 e some da conferência.
    """
    removidos = [
        medico for medico in sessao.reconhecidos if medico.discord_id == discord_id
    ]
    sessao.reconhecidos = [
        medico for medico in sessao.reconhecidos if medico.discord_id != discord_id
    ]
    if not removidos:
        return False

    _sincronizar_nao_reconhecidos_com_ems_original(sessao)
    return True


def _sincronizar_nao_reconhecidos_com_ems_original(sessao: SessaoChamada) -> None:
    """
    Reconstrói `nao_reconhecidos` a partir das linhas originais do print.

    Uma linha do EMS só sai da lista de não identificados se ainda houver
    alguém em `reconhecidos` cobrindo aquele ID (lido ou final) ou o nome.
    Quem foi removido na Etapa 1 volta automaticamente pra Etapa 2.
    """
    if not sessao.entradas_ems_originais:
        return

    ids_cobertos: set[str] = set()
    nomes_cobertos: list[str] = []
    for medico in sessao.reconhecidos:
        if medico.id_fivem:
            ids_cobertos.add(str(medico.id_fivem).strip())
        if medico.id_fivem_lido:
            ids_cobertos.add(str(medico.id_fivem_lido).strip())
        if medico.nome_ems:
            nomes_cobertos.append(medico.nome_ems)
        if medico.nome_discord:
            nomes_cobertos.append(medico.nome_discord)

    # Quem o doutor já marcou como Norte também não volta pra lista
    for entrada_norte in sessao.medicos_norte:
        id_norte = str(entrada_norte.get("id_fivem") or "").strip()
        if id_norte:
            ids_cobertos.add(id_norte)

    reconstruidos: list[dict] = []
    ids_ja_listados: set[str] = set()
    for entrada in sessao.entradas_ems_originais:
        id_lido = str(entrada.get("id_fivem") or "").strip()
        nome_lido = entrada.get("nome_ems") or ""
        if id_lido and id_lido in ids_ja_listados:
            continue
        if nomes_ou_ids_batem_com_reconhecido(
            id_lido, nome_lido, ids_cobertos, nomes_cobertos
        ):
            continue
        reconstruidos.append({"id_fivem": id_lido, "nome_ems": nome_lido})
        if id_lido:
            ids_ja_listados.add(id_lido)

    sessao.nao_reconhecidos = reconstruidos


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
        "`•` Envie **neste canal** um print do comando `/ems`, o mais legível "
        "possível.\n"
        "`•` O sistema vai identificar os IDs FiveM automaticamente.\n"
        f"`•` Você tem **{TIMEOUT_PRINT_EMS_SEGUNDOS // 60} minutos**.",
        discord.Color.gold(),
    )


def _tem_cargo_bypass(discord_id: int, guild: discord.Guild) -> bool:
    """
    Dispensa verificação de presença na chamada.

    Inclui CARGOS_BYPASS_PRESENCA_CHAMADA e toda a Diretoria++ (CARGOS_DIRETORIA).
    Esses membros entram como presentes automaticamente: sem falta, sem +1/- moeda.
    """
    membro = guild.get_member(discord_id)
    if membro is None:
        return False
    if e_diretoria(membro):
        return True
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


def _linha_medico(medico: MedicoNaChamada) -> str:
    marcador = {"corrigido": "🔧", "manual": "➕"}.get(medico.origem, "•")
    quem = (
        f"<@{medico.discord_id}>"
        if medico.discord_id
        else (medico.nome_discord or medico.nome_ems)
    )
    return f"{marcador} `{medico.id_fivem}` — {quem}"


def _linha_desconhecido(erro: dict) -> str:
    return f"❓ `{erro['id_fivem']}` — {erro['nome_ems']}"


# ─────────────────────────────────────────────
# Painel de Coordenação (inalterado, exceto imports já corretos)
# ─────────────────────────────────────────────


class PainelChamadaView(LoggingViewMixin, discord.ui.LayoutView):
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
            f"`🩺` **Modo Chamada ativo** — {membro.mention}\n"
            f"`📋` {status_texto}\n"
            "`ℹ️` Ao realizar a chamada, você vai enviar um print do `/ems` "
            "e o sistema vai cruzar com quem está de plantão."
        )

        componentes = [
            discord.ui.TextDisplay("# 🧭 Painel de Chamada"),
            discord.ui.TextDisplay(linhas),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
        ]

        row = discord.ui.ActionRow()
        botao_chamada = discord.ui.Button(
            label="🩺 Realizar Chamada",
            style=discord.ButtonStyle.success,
            disabled=not liberado,
        )
        botao_chamada.callback = self._ao_realizar_chamada
        row.add_item(botao_chamada)

        componentes.append(row)
        self.container = discord.ui.Container(
            *componentes, accent_color=discord.Color.blurple()
        )
        self.add_item(self.container)

    @classmethod
    async def construir(cls, membro: discord.Member) -> "PainelChamadaView":
        """Prepara o painel com o horário real de liberação da próxima chamada.

        Consulta a regra de cooldown antes de instanciar a view, evitando mostrar um
        botão utilizável quando a chamada ainda não pode começar para este membro.
        """
        proximo_horario, liberado = await calcular_proximo_horario_permitido()
        return cls(membro, proximo_horario, liberado)

    async def _ao_sair(self, interaction: discord.Interaction):
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

    async def _ao_realizar_chamada(self, interaction: discord.Interaction):
        if not membro_e_doutor_ou_acima(interaction.user):
            await responder_erro(
                interaction,
                titulo="Sem permissão",
                linhas=[
                    "Apenas Doutor ou acima pode realizar chamadas.",
                ],
            )
            return

        # Limpa sessão em memória se o lock já tinha expirado no banco
        from src.plantao.chamada.chamada_service import cancelar_chamada

        liberou_expirado = await liberar_lock_se_expirado()
        if liberou_expirado:
            definir_sessao(None)

        conseguiu, outro_doutor_id = await tentar_iniciar_chamada(interaction.user.id)
        if not conseguiu:
            mencao_outro = (
                f"<@{outro_doutor_id}>" if outro_doutor_id else "outro doutor"
            )
            await responder_aviso(
                interaction,
                titulo="Já em andamento",
                linhas=[
                    f"{mencao_outro} já está realizando uma chamada agora.\n"
                    "Se a chamada não for concluída, o sistema **cancela sozinho** "
                    f"em até **{TEMPO_MAXIMO_SESSAO_CHAMADA_MINUTOS} minutos** e "
                    "libera para outro doutor.",
                ],
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Nova sessão em memória (substitui qualquer sessão órfã do mesmo doutor)
        definir_sessao(None)

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

        await editar_mensagem_original(
            interaction,
            view=_construir_view_aguardando_print(),
        )  # 👈 edita, não envia novo

        bot = interaction.client

        def checagem(mensagem: discord.Message) -> bool:
            """Aceita somente o anexo enviado pelo doutor no canal desta sessão."""
            return (
                mensagem.author.id == interaction.user.id
                and mensagem.channel.id == interaction.channel_id
                and len(mensagem.attachments) > 0
            )

        try:
            mensagem_print = await bot.wait_for(
                "message",
                timeout=TIMEOUT_PRINT_EMS_SEGUNDOS,
                check=checagem,
            )
        except TimeoutError:
            await cancelar_chamada(motivo=MOTIVO_CANCEL_TIMEOUT_PRINT)
            definir_sessao(None)
            minutos_print = TIMEOUT_PRINT_EMS_SEGUNDOS // 60
            await editar_mensagem_original(
                interaction,
                view=_construir_view_simples(
                    "⏱️ Chamada cancelada",
                    f"O doutor não enviou o print do `/ems` em **{minutos_print} "
                    "minutos**.\n"
                    "A chamada permanece **em aberto** para outro doutor (ou o mesmo) "
                    "iniciar.",
                    discord.Color.red(),
                ),
            )
            return

        # Baixa os bytes já no envio do doutor (antes do OCR e antes de apagar a msg).
        # Assim o tópico no final só reenvia a cópia local — não depende da CDN.
        anexo = mensagem_print.attachments[0]
        sessao.print_ems_url = anexo.url
        sessao.print_ems_nome_arquivo = anexo.filename or "print_ems.png"
        sessao.print_ems_mensagem = mensagem_print
        try:
            sessao.print_ems_bytes = await anexo.read()
        except (discord.HTTPException, OSError) as erro_leitura:
            registrador.warning(f"⚠️ [chamada] anexo.read falhou: {erro_leitura}")
            sessao.print_ems_bytes = None

        if not sessao.print_ems_bytes:
            # fallback imediato pela URL/proxy enquanto o link ainda é válido
            url_tentativa = getattr(anexo, "proxy_url", None) or anexo.url
            dados_url, nome_url = await _baixar_bytes_da_url(url_tentativa)
            if dados_url:
                sessao.print_ems_bytes = dados_url
                sessao.print_ems_nome_arquivo = nome_url
                registrador.info(
                    f"✅ [chamada] print baixado via URL ({len(dados_url)} bytes)"
                )
            else:
                registrador.warning(
                    "⚠️ [chamada] não foi possível guardar bytes do print no envio"
                )
        else:
            registrador.info(
                f"✅ [chamada] print em memória "
                f"({len(sessao.print_ems_bytes)} bytes, "
                f"{sessao.print_ems_nome_arquivo})"
            )

        await _processar_print_ems(interaction, anexo.url)


# ─────────────────────────────────────────────
# Processamento do OCR + cruzamento
# ─────────────────────────────────────────────


async def _processar_print_ems(interaction: discord.Interaction, url_imagem: str):
    sessao = obter_sessao()
    guild = interaction.guild

    await editar_mensagem_original(
        interaction,
        view=_construir_view_processando(),
    )  # 👈 edita

    try:
        resultado = await asyncio.wait_for(
            extrair_medicos_do_print_ems(url_imagem), timeout=90
        )
    except asyncio.TimeoutError:
        await cancelar_chamada(motivo=MOTIVO_CANCEL_ERRO)
        await destruir_print_com_aviso(sessao.print_ems_mensagem, delay=10)
        definir_sessao(None)
        await editar_mensagem_original(
            interaction,
            view=_construir_view_simples(
                "❌ Chamada Cancelada",
                "Processamento excedeu o tempo limite.",
                discord.Color.red(),
            ),
        )
        return
    except OcrEmsError as exc:
        await cancelar_chamada(motivo=MOTIVO_CANCEL_ERRO)
        await destruir_print_com_aviso(sessao.print_ems_mensagem, delay=10)
        definir_sessao(None)
        await editar_mensagem_original(
            interaction,
            view=_construir_view_simples(
                "❌ Chamada Cancelada",
                f"Erro ao ler a imagem: {exc}",
                discord.Color.red(),
            ),
        )
        return
    except Exception:
        logger.exception("💥 Falha inesperada no OCR")
        await cancelar_chamada(motivo=MOTIVO_CANCEL_ERRO)
        await destruir_print_com_aviso(sessao.print_ems_mensagem, delay=10)
        definir_sessao(None)
        await editar_mensagem_original(
            interaction,
            view=_construir_view_simples(
                "❌ Chamada Cancelada",
                "Erro inesperado ao processar a imagem.",
                discord.Color.red(),
            ),
        )
        return

    medicos_ems = resultado["medicos"]
    sessao.total_medicos_ems = len(medicos_ems)

    # Guarda cada linha do print como veio do OCR — base da Etapa 2.
    sessao.entradas_ems_originais = [
        {
            "id_fivem": str(medico_da_api.get("id") or "").strip(),
            "nome_ems": medico_da_api.get("nome") or "",
        }
        for medico_da_api in medicos_ems
    ]

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

    for medico_validado in validados:
        id_final = medico_validado.id_corrigido or medico_validado.id_lido
        ids_no_ems.add(id_final)
        if medico_validado.status in ("confirmado", "corrigido"):
            membro_db = guild.get_member(medico_validado.membro.discord_id)
            reconhecidos.append(
                MedicoNaChamada(
                    id_fivem=medico_validado.membro.id_fivem,
                    discord_id=medico_validado.membro.discord_id,
                    nome_ems=medico_validado.nome_lido,
                    nome_discord=membro_db.display_name if membro_db else None,
                    confianca=1.0 if medico_validado.status == "confirmado" else 0.7,
                    origem="ocr"
                    if medico_validado.status == "confirmado"
                    else "corrigido",
                    motivo=medico_validado.motivo,
                    id_fivem_lido=medico_validado.id_lido,
                )
            )
        else:
            nao_reconhecidos.append(
                {
                    "id_fivem": medico_validado.id_lido,
                    "nome_ems": medico_validado.nome_lido,
                }
            )

    sessao.reconhecidos = reconhecidos
    sessao.nao_reconhecidos = nao_reconhecidos
    sessao.total_toggle_ligado = len(estados_ligados)

    # Segunda passagem: tenta resgatar "não identificados" pelo servidor
    # (nome parcial / FID no nick) e tira duplicatas que já estão nos presentes
    _resgatar_nao_reconhecidos_pelo_servidor(sessao, guild)
    _limpar_nao_reconhecidos_ja_presentes(sessao)

    ids_fivem_com_toggle = {
        erro.id_fivem: erro.discord_id for erro in estados_ligados if erro.id_fivem
    }
    ids_com_toggle_fora_do_ems = set(ids_fivem_com_toggle.keys()) - ids_no_ems
    # Quem realiza a chamada não conta como ausente do EMS
    doutor_id = sessao.doutor_id
    sessao.toggle_ligado_mas_nao_no_ems = [
        MedicoNaChamada(
            id_fivem=id_fivem, discord_id=ids_fivem_com_toggle[id_fivem], nome_ems="—"
        )
        for id_fivem in ids_com_toggle_fora_do_ems
        if ids_fivem_com_toggle[id_fivem] != doutor_id
    ]

    await _processar_ausentes_do_ems(interaction, sessao)

    view_etapa_1 = _construir_etapa_1(
        sessao, guild
    )  # retirado await pois nao é mais async def
    await editar_mensagem_original(
        interaction,
        view=view_etapa_1,
    )


async def _processar_ausentes_do_ems(
    interaction: discord.Interaction, sessao: SessaoChamada
):
    guild = interaction.guild
    for medico in sessao.toggle_ligado_mas_nao_no_ems:
        membro = guild.get_member(medico.discord_id)
        if membro is None:
            continue
        # Quem está fazendo a chamada nunca leva falta por esta regra
        if membro.id == sessao.doutor_id:
            continue
        # Diretoria / bypass: sem falta e sem desligar plantão por chamada
        if _tem_cargo_bypass(membro.id, guild):
            continue
        try:
            await membro.send(
                "⚠️ Durante a chamada de auditoria, você estava com o plantão ativo no "
                "Discord "
                "mas não foi encontrado no `/ems` da cidade. Seu plantão foi encerrado "
                "automaticamente."
            )
        except discord.Forbidden as erro_em_processar_ausentes_do_ems:
            # Enfeite que falhou: avisar quem faltou na chamada.
            # A acao principal ja tinha dado certo, entao so registro.
            ignorar_falha_cosmetica(
                erro_em_processar_ausentes_do_ems,
                o_que_falhou="avisar quem faltou na chamada",
            )
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
    for medico in sessao.reconhecidos:
        chave = medico.discord_id if medico.discord_id else medico.id_fivem
        if chave in vistos:
            continue
        vistos.add(chave)
        unicos.append(medico)
    sessao.reconhecidos = unicos


def _coletar_ids_e_nomes_reconhecidos(
    sessao: SessaoChamada,
) -> tuple[set[str], list[str]]:
    ids: set[str] = set()
    nomes: list[str] = []
    for medico in sessao.reconhecidos:
        if medico.id_fivem:
            ids.add(str(medico.id_fivem).strip())
        if medico.nome_ems:
            nomes.append(medico.nome_ems)
        if medico.nome_discord:
            nomes.append(medico.nome_discord)
    return ids, nomes


def _limpar_nao_reconhecidos_ja_presentes(sessao: SessaoChamada) -> None:
    """
    Se alguém já está em `reconhecidos` (presente identificado),
    não pode ficar em `nao_reconhecidos` / Norte com o mesmo FID ou nome.
    """
    ids_presentes, nomes_presentes = _coletar_ids_e_nomes_reconhecidos(sessao)
    filtrados = []
    for entrada in sessao.nao_reconhecidos:
        id_entrada = str(entrada.get("id_fivem") or "").strip()
        nome_entrada = entrada.get("nome_ems") or ""
        if nomes_ou_ids_batem_com_reconhecido(
            id_entrada, nome_entrada, ids_presentes, nomes_presentes
        ):
            continue
        filtrados.append(entrada)
    sessao.nao_reconhecidos = filtrados


def _resgatar_nao_reconhecidos_pelo_servidor(
    sessao: SessaoChamada, guild: discord.Guild
) -> None:
    """
    Para cada linha ainda 'não identificada', tenta achar no servidor:
    - FID no apelido (Nome | 1234)
    - nome parecido com display_name / nick
    Se achar, move para reconhecidos.
    """
    if guild is None or not sessao.nao_reconhecidos:
        return

    ainda_desconhecidos = []
    discord_ids_ja = {
        medico.discord_id
        for medico in sessao.reconhecidos
        if medico.discord_id is not None
    }

    for entrada in sessao.nao_reconhecidos:
        id_lido = str(entrada.get("id_fivem") or "").strip()
        nome_lido = entrada.get("nome_ems") or ""
        membro_achado = _buscar_membro_no_servidor(guild, id_lido, nome_lido)

        if membro_achado is None:
            ainda_desconhecidos.append(entrada)
            continue
        if membro_achado.id in discord_ids_ja:
            # Já está nos presentes — não duplica nem manda pro Norte
            continue

        id_pelo_membro = _resolver_id_fivem_do_membro(
            sessao, membro_achado.id, membro_achado
        )
        id_fivem_final = id_lido or id_pelo_membro or "—"
        sessao.reconhecidos.append(
            MedicoNaChamada(
                id_fivem=str(id_fivem_final),
                discord_id=membro_achado.id,
                nome_ems=nome_lido or membro_achado.display_name,
                nome_discord=membro_achado.display_name,
                confianca=0.65,
                origem="corrigido",
                motivo="Resgatado pelo nome/FID no servidor",
                id_fivem_lido=id_lido or None,
            )
        )
        discord_ids_ja.add(membro_achado.id)

    sessao.nao_reconhecidos = ainda_desconhecidos
    _deduplicar_reconhecidos(sessao)


def _buscar_membro_no_servidor(
    guild: discord.Guild,
    id_fivem: str,
    nome_ems: str,
) -> discord.Member | None:
    """Procura membro na guild pelo FID do nick ou pelo nome do EMS."""
    id_limpo = str(id_fivem or "").strip()
    nome_norm = normalizar_nome(nome_ems)

    for membro in guild.members:
        if membro.bot:
            continue
        nome_exibido = membro.nick or membro.display_name or membro.name
        fid_no_nick = extrair_id_do_apelido(nome_exibido)
        if id_limpo and fid_no_nick and fid_no_nick == id_limpo:
            return membro
        if nome_norm and nomes_parecidos(nome_ems, nome_exibido):
            return membro
        # Também compara só a parte do nome (sem tag / sem FID)
        nome_sem_fid = nome_exibido.split("|")[0].strip()
        if nome_norm and nomes_parecidos(nome_ems, nome_sem_fid):
            return membro
    return None


# ─────────────────────────────────────────────
# ETAPA 1 — Verificação
# ─────────────────────────────────────────────


def _construir_etapa_1(
    sessao: SessaoChamada, guild: discord.Guild
) -> discord.ui.LayoutView:
    sessao.etapa_atual = 1
    _deduplicar_reconhecidos(sessao)
    # Mantém o contador de "ainda não identificados" alinhado com o print
    _sincronizar_nao_reconhecidos_com_ems_original(sessao)

    corrigidos = [
        medico for medico in sessao.reconhecidos if medico.origem == "corrigido"
    ]
    demais = [medico for medico in sessao.reconhecidos if medico.origem != "corrigido"]

    blocos_demais = _construir_blocos_texto(
        [_linha_medico(medico) for medico in demais]
    )

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
            f"🔧 `{medico.id_fivem}` — <@{medico.discord_id}>\n> "
            f"_{medico.motivo or 'correção automática'}"
            f"_"
            for medico in corrigidos
        )
        componentes.append(
            discord.ui.TextDisplay(
                f"**⚠️ Correções automáticas — confira com "
                f"atenção**\n{linhas_corrigidos}"
            )
        )

        if len(corrigidos) <= 25:
            select_remover_corrigidos = discord.ui.Select(
                placeholder="Selecione quem está ERRADO aqui pra remover",
                options=[
                    discord.SelectOption(
                        label=f"{medico.nome_discord or medico.nome_ems} | {medico.id_fivem}",
                        value=str(medico.discord_id),
                    )
                    for medico in corrigidos
                ],
                min_values=0,
                max_values=len(corrigidos),
            )
            select_remover_corrigidos.callback = _ao_remover_corrigidos
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
    user_select.callback = _ao_userselect_adicionar
    row_select.add_item(user_select)
    componentes.append(row_select)

    row_botoes = discord.ui.ActionRow()
    botao_discord_id = discord.ui.Button(
        label="🆔 Buscar por Discord ID", style=discord.ButtonStyle.secondary
    )
    botao_discord_id.callback = _ao_abrir_modal_discord_id
    row_botoes.add_item(botao_discord_id)

    botao_fivem = discord.ui.Button(
        label="🎮 Buscar por ID FiveM", style=discord.ButtonStyle.secondary
    )
    botao_fivem.callback = _ao_abrir_modal_id_fivem
    row_botoes.add_item(botao_fivem)

    botao_remover = discord.ui.Button(
        label="❌ Remover", style=discord.ButtonStyle.danger
    )
    botao_remover.callback = _ao_abrir_modal_remover
    row_botoes.add_item(botao_remover)
    componentes.append(row_botoes)

    row_continuar = discord.ui.ActionRow()
    botao_continuar = discord.ui.Button(
        label="➡️ Continuar", style=discord.ButtonStyle.primary
    )
    botao_continuar.callback = _ao_ir_etapa_2
    row_continuar.add_item(botao_continuar)
    componentes.append(row_continuar)

    layout = _ViewSessaoChamada()
    layout.add_item(
        discord.ui.Container(*componentes, accent_color=discord.Color.blurple())
    )
    return layout


def _resolver_id_fivem_do_membro(
    sessao: SessaoChamada, discord_id: int, membro: discord.Member
) -> str | None:
    """Tenta achar o id_fivem de um membro específico: primeiro no cache de
    membros_conhecidos (Recrutamento/apelido), senão tenta extrair do apelido atual."""
    for medico in sessao.membros_conhecidos:
        if medico.discord_id == discord_id:
            return medico.id_fivem
    from src.plantao.chamada.ocr.leitura_de_membros_service import extrair_id_do_apelido

    nome_exibido = membro.nick or membro.display_name or membro.name
    return extrair_id_do_apelido(nome_exibido)


def _adicionar_medico_manual(
    sessao: SessaoChamada, guild: discord.Guild, discord_id: int, id_fivem: str | None
) -> bool:
    """
    Retorna True se adicionou de fato, False se a pessoa já estava na lista (evita
    duplicata).
    """
    ja_existe = any(medico.discord_id == discord_id for medico in sessao.reconhecidos)
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
        id_fivem_lido=id_fivem,
    )
    sessao.reconhecidos.append(novo)
    # Atualiza a lista de não identificados com base no print original
    _sincronizar_nao_reconhecidos_com_ems_original(sessao)
    return True


async def _ao_userselect_adicionar(interaction: discord.Interaction):
    sessao = obter_sessao()
    if sessao is None:
        await responder_aviso(
            interaction,
            titulo="Sessão expirada",
            linhas=[
                "Sessão expirada.",
            ],
        )
        return

    membro_selecionado = interaction.data["values"][0]
    discord_id = int(membro_selecionado)

    _adicionar_medico_manual(sessao, interaction.guild, discord_id, None)

    await interaction.response.defer(ephemeral=True)
    await editar_mensagem_original(
        interaction,
        view=_construir_etapa_1(sessao, interaction.guild),
    )


class ModalBuscarPorDiscordId(discord.ui.Modal, title="Buscar por Discord ID"):
    discord_id_input = discord.ui.TextInput(
        label="Discord ID", placeholder="Ex: 859100649366356000", required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        """Inclui na conferência o membro localizado por identificador do Discord.

        Confere se a sessão ainda existe e se o ID pertence à guilda antes de alterar
        a lista em memória. Em seguida atualiza o mesmo painel, evitando acrescentar
        pessoas inexistentes ou duplicadas durante a revisão manual do OCR.
        """
        sessao = obter_sessao()
        if sessao is None:
            await responder_aviso(
                interaction,
                titulo="Sessão expirada",
                linhas=[
                    "Sessão expirada.",
                ],
            )
            return

        valor = self.discord_id_input.value.strip()
        if not valor.isdigit():
            await responder_erro(
                interaction,
                titulo="Dado inválido",
                linhas=[
                    "Discord ID inválido.",
                ],
            )
            return

        discord_id = int(valor)
        membro = interaction.guild.get_member(discord_id)
        if membro is None:
            await responder_erro(
                interaction,
                titulo="Não encontrado",
                linhas=[
                    "Membro não encontrado neste servidor.",
                ],
            )
            return

        _adicionar_medico_manual(sessao, interaction.guild, discord_id, None)
        await interaction.response.defer(ephemeral=True)
        await editar_mensagem_original(
            interaction,
            view=_construir_etapa_1(sessao, interaction.guild),
        )


class ModalBuscarPorIdFivem(discord.ui.Modal, title="Buscar por ID FiveM"):
    id_fivem_input = discord.ui.TextInput(
        label="ID FiveM", placeholder="Ex: 054623", required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        """Inclui na conferência o membro conhecido pelo identificador FiveM.

        Procura o ID entre as associações reunidas para a sessão, em vez de confiar
        apenas no texto digitado. Isso preserva o vínculo com o Discord e impede que
        um ID sem membro reconhecido seja contado como presença.
        """
        sessao = obter_sessao()
        if sessao is None:
            await responder_aviso(
                interaction,
                titulo="Sessão expirada",
                linhas=[
                    "Sessão expirada.",
                ],
            )
            return

        id_fivem = self.id_fivem_input.value.strip()
        membro_conhecido = next(
            (
                medico
                for medico in sessao.membros_conhecidos
                if medico.id_fivem == id_fivem
            ),
            None,
        )

        if membro_conhecido is None:
            await responder_aviso(
                interaction,
                titulo="Nada para mostrar",
                linhas=[
                    "Nenhum membro no servidor com esse ID FiveM (nem no "
                    "Recrutamento, nem no apelido).",
                ],
            )
            return

        _adicionar_medico_manual(
            sessao, interaction.guild, membro_conhecido.discord_id, id_fivem
        )
        await interaction.response.defer(ephemeral=True)
        await editar_mensagem_original(
            interaction,
            view=_construir_etapa_1(sessao, interaction.guild),
        )


class ModalRemoverMedico(discord.ui.Modal, title="Remover da Lista"):
    identificador = discord.ui.TextInput(
        label="ID FiveM, Discord ID ou Menção",
        placeholder="Ex: 054623 ou @membro ou 859100649366356000",
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        """Remove da conferência o médico indicado por menção, Discord ID ou FiveM.

        Normaliza a entrada e só altera a lista em memória quando encontra exatamente
        alguém já reconhecido. Depois redesenha a etapa atual para evitar que uma
        correção visual deixe um médico removido ainda parecendo presente.
        """
        sessao = obter_sessao()
        if sessao is None:
            await responder_aviso(
                interaction,
                titulo="Sessão expirada",
                linhas=[
                    "Sessão expirada.",
                ],
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
                (
                    medico
                    for medico in sessao.reconhecidos
                    if medico.discord_id == int(valor)
                ),
                None,
            )
        elif valor.isdigit():
            alvo = next(
                (medico for medico in sessao.reconhecidos if medico.id_fivem == valor),
                None,
            )

        if alvo is None:
            await responder_erro(
                interaction,
                titulo="Identificador não confere",
                linhas=[
                    "Ninguém na lista atual bate com esse identificador.",
                ],
            )
            return

        _remover_medico(sessao, alvo.discord_id)
        await interaction.response.defer(ephemeral=True)
        await editar_mensagem_original(
            interaction,
            view=_construir_etapa_1(sessao, interaction.guild),
        )


async def _ao_abrir_modal_remover(interaction: discord.Interaction):
    await interaction.response.send_modal(ModalRemoverMedico())


async def _ao_abrir_modal_discord_id(interaction: discord.Interaction):
    await interaction.response.send_modal(ModalBuscarPorDiscordId())


async def _ao_abrir_modal_id_fivem(interaction: discord.Interaction):
    await interaction.response.send_modal(ModalBuscarPorIdFivem())


async def _ao_ir_etapa_2(interaction: discord.Interaction):
    sessao = obter_sessao()
    # Garante que todo ID do print ainda sem dono aparece na Etapa 2,
    # inclusive os que o doutor tirou de uma correção automática errada.
    _sincronizar_nao_reconhecidos_com_ems_original(sessao)
    await interaction.response.defer(ephemeral=True)
    await editar_mensagem_original(
        interaction,
        view=_construir_etapa_2(sessao, interaction.guild),
    )


async def _ao_remover_corrigidos(interaction: discord.Interaction):
    ids_selecionados = {
        int(valor_selecionado) for valor_selecionado in interaction.data["values"]
    }
    sessao = obter_sessao()

    for discord_id in ids_selecionados:
        _remover_medico(sessao, discord_id)

    await interaction.response.defer(ephemeral=True)
    await editar_mensagem_original(
        interaction,
        view=_construir_etapa_1(sessao, interaction.guild),
    )


# ─────────────────────────────────────────────
# ETAPA 2 — Desconhecidos
# ─────────────────────────────────────────────


def _construir_etapa_2(
    sessao: SessaoChamada, guild: discord.Guild
) -> discord.ui.LayoutView:
    sessao.etapa_atual = 2
    _sincronizar_nao_reconhecidos_com_ems_original(sessao)
    linhas = [_linha_desconhecido(erro) for erro in sessao.nao_reconhecidos]
    blocos = _construir_blocos_texto(linhas)

    resumo = (
        f"`❓` Restam **{len(sessao.nao_reconhecidos)}** não identificados.\n"
        "Se não forem médicos do nosso hospital, marque como Hospital Norte pra "
        "liberar a próxima etapa."
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
    botao_voltar.callback = lambda indice: _voltar_para_etapa(indice, 1)
    row.add_item(botao_voltar)

    if sessao.nao_reconhecidos:
        botao_norte = discord.ui.Button(
            label="🏥 Médico CMN (Hospital Norte)", style=discord.ButtonStyle.secondary
        )
        botao_norte.callback = _ao_marcar_como_norte
        row.add_item(botao_norte)

    botao_continuar = discord.ui.Button(
        label="➡️ Continuar",
        style=discord.ButtonStyle.primary,
        disabled=bool(sessao.nao_reconhecidos),
    )
    botao_continuar.callback = _ao_ir_etapa_3
    row.add_item(botao_continuar)
    componentes.append(row)

    layout = _ViewSessaoChamada()
    layout.add_item(
        discord.ui.Container(*componentes, accent_color=discord.Color.blurple())
    )
    return layout


async def _ao_marcar_como_norte(interaction: discord.Interaction):
    sessao = obter_sessao()
    # Última limpeza: quem já está presente não vai pro Norte
    _limpar_nao_reconhecidos_ja_presentes(sessao)
    if interaction.guild is not None:
        _resgatar_nao_reconhecidos_pelo_servidor(sessao, interaction.guild)
        _limpar_nao_reconhecidos_ja_presentes(sessao)

    sessao.medicos_norte.extend(sessao.nao_reconhecidos)
    sessao.nao_reconhecidos = []
    await interaction.response.defer(ephemeral=True)
    await editar_mensagem_original(
        interaction,
        view=_construir_etapa_2(sessao, interaction.guild),
    )


async def _voltar_para_etapa(interaction: discord.Interaction, etapa: int):
    sessao = obter_sessao()
    await interaction.response.defer(ephemeral=True)
    construtor = {1: _construir_etapa_1, 2: _construir_etapa_2, 3: _construir_etapa_3}[
        etapa
    ]
    await editar_mensagem_original(
        interaction,
        view=construtor(sessao, interaction.guild),
    )


async def _ao_ir_etapa_3(interaction: discord.Interaction):
    sessao = obter_sessao()
    await interaction.response.defer(ephemeral=True)
    await editar_mensagem_original(
        interaction,
        view=_construir_etapa_3(sessao, interaction.guild),
    )


# ─────────────────────────────────────────────
# ETAPA 3 — Em Serviço (confirmação de presença)
# ─────────────────────────────────────────────


def _construir_etapa_3(
    sessao: SessaoChamada, guild: discord.Guild
) -> discord.ui.LayoutView:
    sessao.etapa_atual = 3

    sessao.bypass_presenca = [
        medico_da_lista
        for medico_da_lista in sessao.reconhecidos
        if _tem_cargo_bypass(medico_da_lista.discord_id, guild)
    ]
    # Quem faz a chamada entra no bypass: sempre presente, nunca falta
    ids_bypass = {
        medico_com_bypass.discord_id for medico_com_bypass in sessao.bypass_presenca
    }
    for medico in sessao.reconhecidos:
        if (
            medico.discord_id == sessao.doutor_id
            and medico.discord_id not in ids_bypass
        ):
            sessao.bypass_presenca.append(medico)
            ids_bypass.add(medico.discord_id)

    # Usa TODOS os identificados na Etapa 1 (já excluindo Norte / bypass)
    sessao.presentes_no_ems_toggle_ligado = [
        medico_da_lista
        for medico_da_lista in sessao.reconhecidos
        if medico_da_lista.discord_id not in ids_bypass
    ]
    # Garante que o doutor nunca fique marcado como faltante no select
    sessao.faltantes_ids.discard(sessao.doutor_id)

    linha_bypass = (
        f"`🛡️` **{len(sessao.bypass_presenca)}** já contam como presentes "
        f"automaticamente (cargo com dispensa).\n"
        if sessao.bypass_presenca
        else ""
    )

    resumo = (
        f"{linha_bypass}"
        f"`🟢` **{len(sessao.presentes_no_ems_toggle_ligado)}** aguardando confirmação "
        f"de presença.\n"
        "Todos começam marcados como **presentes**. Verifique call, rádio in-game ou "
        "chame na interna "
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
    botao_voltar.callback = lambda indice: _voltar_para_etapa(indice, 2)
    row_botoes.add_item(botao_voltar)

    if not sessao.presentes_no_ems_toggle_ligado:
        botao_continuar = discord.ui.Button(
            label="➡️ Continuar", style=discord.ButtonStyle.primary
        )
        botao_continuar.callback = _ao_ir_etapa_4
        row_botoes.add_item(botao_continuar)
        componentes.append(row_botoes)

        layout = _ViewSessaoChamada()
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
                f"⚠️ **Atenção:** {len(sessao.presentes_no_ems_toggle_ligado)} médicos "
                f"identificados, "
                f"mas o menu só mostra os primeiros 25. Os demais permanecem como "
                f"presentes por padrão."
            )
        )

    opcoes = [
        discord.SelectOption(
            label=f"{medico_da_lista.nome_discord or medico_da_lista.nome_ems} | {medico_da_lista.id_fivem}",
            value=str(medico_da_lista.discord_id),
            default=medico_da_lista.discord_id not in sessao.faltantes_ids,
        )
        for medico_da_lista in lista_para_select
    ]

    select_presenca = discord.ui.Select(
        placeholder="Todos marcados = presentes. Desmarque quem não respondeu.",
        options=opcoes,
        min_values=0,
        max_values=len(opcoes),
    )
    select_presenca.callback = _ao_atualizar_faltantes
    row_select = discord.ui.ActionRow()
    row_select.add_item(select_presenca)
    componentes.append(row_select)

    botao_continuar = discord.ui.Button(
        label="➡️ Continuar", style=discord.ButtonStyle.primary
    )
    botao_continuar.callback = _ao_ir_etapa_4
    row_botoes.add_item(botao_continuar)
    componentes.append(row_botoes)

    layout = _ViewSessaoChamada()
    layout.add_item(
        discord.ui.Container(*componentes, accent_color=discord.Color.blurple())
    )
    return layout


async def _ao_atualizar_faltantes(interaction: discord.Interaction):
    sessao = obter_sessao()
    ids_selecionados = {
        int(valor_selecionado) for valor_selecionado in interaction.data["values"]
    }  # quem ficou marcado = presente
    todos_ids = {medico.discord_id for medico in sessao.presentes_no_ems_toggle_ligado}
    sessao.faltantes_ids = todos_ids - ids_selecionados  # quem foi desmarcado = falta

    await interaction.response.defer(ephemeral=True)
    await editar_mensagem_original(
        interaction,
        view=_construir_etapa_3(sessao, interaction.guild),
    )


async def _ao_ir_etapa_4(interaction: discord.Interaction):
    sessao = obter_sessao()
    await interaction.response.defer(ephemeral=True)
    await editar_mensagem_original(
        interaction,
        view=_construir_etapa_4(sessao, interaction.guild),
    )


# ─────────────────────────────────────────────
# ETAPA 4 — Conclusão
# ─────────────────────────────────────────────


def _construir_etapa_4(
    sessao: SessaoChamada, guild: discord.Guild
) -> discord.ui.LayoutView:
    sessao.etapa_atual = 4

    presentes = sessao.bypass_presenca + [
        medico
        for medico in sessao.presentes_no_ems_toggle_ligado
        if medico.discord_id not in sessao.faltantes_ids
    ]
    faltantes = [
        medico
        for medico in sessao.presentes_no_ems_toggle_ligado
        if medico.discord_id in sessao.faltantes_ids
    ]

    linhas_presentes = (
        "\n".join(_linha_medico(medico) for medico in presentes) or "_(nenhum)_"
    )
    linhas_faltantes = (
        "\n".join(_linha_medico(medico) for medico in faltantes) or "_(nenhum)_"
    )
    linhas_norte = (
        "\n".join(_linha_desconhecido(erro) for erro in sessao.medicos_norte)
        or "_(nenhum)_"
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
    botao_voltar.callback = lambda indice: _voltar_para_etapa(indice, 3)
    row.add_item(botao_voltar)

    botao_finalizar = discord.ui.Button(
        label="✅ Finalizar Chamada", style=discord.ButtonStyle.success
    )
    botao_finalizar.callback = _ao_finalizar_chamada
    row.add_item(botao_finalizar)
    componentes.append(row)

    layout = _ViewSessaoChamada()
    layout.add_item(
        discord.ui.Container(*componentes, accent_color=discord.Color.blurple())
    )
    return layout


async def _ao_finalizar_chamada(interaction: discord.Interaction):
    sessao = obter_sessao()
    if sessao is None:
        await responder_aviso(
            interaction,
            titulo="Chamada já encerrada",
            linhas=["Esta sessão de chamada não está mais ativa."],
        )
        return

    # Trava reenvio: marca etapa e desativa o botão na hora
    if sessao.finalizando:
        await responder_aviso(
            interaction,
            titulo="Já está enviando",
            linhas=["A chamada já está sendo processada. Aguarde…"],
        )
        return
    sessao.finalizando = True

    guild = interaction.guild
    await interaction.response.defer(ephemeral=True)

    # Aviso ephemeral de processando (followup após defer)
    try:
        await responder_info(
            interaction,
            titulo="Processando envio",
            linhas=[
                "Enviando o registro da chamada para o canal.",
                "Não clique de novo — o botão foi desativado.",
            ],
            delay=20,
        )
    except discord.HTTPException as erro_ao_avisar_do_envio:
        # Esse card e so um "aguarde": a chamada continua sendo enviada logo
        # abaixo. Se o aviso nao aparece, o resultado nao muda.
        ignorar_falha_cosmetica(
            erro_ao_avisar_do_envio,
            o_que_falhou="mostrar o aviso de que a chamada esta sendo enviada",
        )

    try:
        await editar_mensagem_original(
            interaction,
            view=_construir_view_simples(
                "⏳ Enviando chamada…",
                "Aguarde o registro no canal. O botão foi desativado.",
                discord.Color.orange(),
            ),
        )
    except discord.HTTPException as erro_ao_finalizar_chamada:
        # Enfeite que falhou: encerrar a mensagem da chamada.
        # A acao principal ja tinha dado certo, entao so registro.
        ignorar_falha_cosmetica(
            erro_ao_finalizar_chamada,
            o_que_falhou="encerrar a mensagem da chamada",
        )

    # Nunca falta pro responsável pela chamada
    sessao.faltantes_ids.discard(sessao.doutor_id)

    presentes = sessao.bypass_presenca + [
        medico_presente
        for medico_presente in sessao.presentes_no_ems_toggle_ligado
        if medico_presente.discord_id not in sessao.faltantes_ids
    ]
    faltantes = [
        medico_presente
        for medico_presente in sessao.presentes_no_ems_toggle_ligado
        if medico_presente.discord_id in sessao.faltantes_ids
        and medico_presente.discord_id != sessao.doutor_id
    ]

    for medico in faltantes:
        membro = guild.get_member(medico.discord_id) if guild else None
        if membro is None:
            continue
        if membro.id == sessao.doutor_id:
            continue
        # Diretoria / bypass: nunca falta, nunca punição, nunca perde plantão por
        # chamada
        if _tem_cargo_bypass(membro.id, guild):
            continue
        await registrar_falta(
            membro.id, sessao.chamada_id, "Não respondeu à chamada (call/rádio)", guild
        )
        await desligar_servico(membro)
        try:
            await membro.send(
                "🔴 Você foi desconectado e seu plantão encerrado por não responder "
                "à chamada de auditoria."
            )
        except discord.Forbidden as erro_ao_finalizar_chamada:
            # Enfeite que falhou: encerrar a mensagem da chamada.
            # A acao principal ja tinha dado certo, entao so registro.
            ignorar_falha_cosmetica(
                erro_ao_finalizar_chamada,
                o_que_falhou="encerrar a mensagem da chamada",
            )

    for medico in presentes:
        # Diretoria / bypass: presença automática, sem +1 moeda de chamada
        if _tem_cargo_bypass(medico.discord_id, guild):
            continue
        if medico.discord_id == sessao.doutor_id:
            # Moeda do doutor é creditada abaixo (só uma vez)
            continue
        async with async_session() as session:
            resultado = await session.execute(
                select(EstadoPlantao).where(
                    EstadoPlantao.discord_id == medico.discord_id
                )
            )
            estado = resultado.scalar_one_or_none()
            if estado:
                estado.saldo_moedas = int(estado.saldo_moedas or 0) + 1
            await session.commit()

    async with async_session() as session:
        resultado = await session.execute(
            select(EstadoPlantao).where(EstadoPlantao.discord_id == sessao.doutor_id)
        )
        estado_doutor = resultado.scalar_one_or_none()
        if estado_doutor:
            estado_doutor.saldo_moedas = int(estado_doutor.saldo_moedas or 0) + 1
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

    # Limpa Norte de quem já está em presentes (FID / nome)
    ids_presentes, nomes_presentes = _coletar_ids_e_nomes_reconhecidos(sessao)
    for medico in presentes:
        if medico.id_fivem:
            ids_presentes.add(str(medico.id_fivem).strip())
        if medico.nome_ems:
            nomes_presentes.append(medico.nome_ems)
        if medico.nome_discord:
            nomes_presentes.append(medico.nome_discord)
    sessao.medicos_norte = [
        entrada
        for entrada in sessao.medicos_norte
        if not nomes_ou_ids_batem_com_reconhecido(
            str(entrada.get("id_fivem") or "").strip(),
            entrada.get("nome_ems") or "",
            ids_presentes,
            nomes_presentes,
        )
    ]

    await _enviar_log_chamada_canal(guild, sessao, presentes, faltantes)

    if sessao.print_ems_mensagem is not None:
        asyncio.create_task(
            excluir_mensagem(sessao.print_ems_mensagem, delay=60)
        )  # limpeza silenciosa, sucesso

    await finalizar_chamada(marcar_ultima_chamada=True)

    await editar_mensagem_original(
        interaction,
        view=_construir_view_simples(
            "✅ Chamada Finalizada",
            "Registrada em CANAL_CHAMADAS_HP_SUL (print no tópico) e LOG_CHAMADAS.",
            discord.Color.green(),
        ),
    )

    definir_sessao(None)


async def _baixar_bytes_da_url(url: str) -> tuple[bytes | None, str]:
    """
    Fallback: baixa a imagem pela URL (pode falhar se o link já expirou).
    Retorna (bytes, nome_sugerido).
    """
    if not url:
        return None, "print_ems.png"
    try:
        async with aiohttp.ClientSession() as cliente:
            async with cliente.get(
                url,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resposta:
                if resposta.status != 200:
                    registrador.warning(
                        f"⚠️ [chamada] download URL status={resposta.status}"
                    )
                    return None, "print_ems.png"
                dados = await resposta.read()
                if not dados:
                    return None, "print_ems.png"
                tipo = (resposta.headers.get("Content-Type") or "").lower()
                if "png" in tipo:
                    nome = "print_ems.png"
                elif "jpeg" in tipo or "jpg" in tipo:
                    nome = "print_ems.jpg"
                elif "webp" in tipo:
                    nome = "print_ems.webp"
                elif "gif" in tipo:
                    nome = "print_ems.gif"
                else:
                    caminho = url.split("?")[0]
                    ext = os.path.splitext(caminho)[1] or ".png"
                    nome = f"print_ems{ext}"
                return dados, nome
    except Exception as erro:
        registrador.warning(f"⚠️ [chamada] download da URL do print falhou: {erro}")
        return None, "print_ems.png"


def _nome_arquivo_seguro(nome: str | None) -> str:
    """Garante um nome de arquivo aceito pelo Discord."""
    bruto = (nome or "print_ems.png").strip() or "print_ems.png"
    # remove path e caracteres estranhos
    bruto = os.path.basename(bruto)
    seguro = "".join(
        caractere if caractere.isalnum() or caractere in "._-" else "_"
        for caractere in bruto
    )
    if "." not in seguro:
        seguro = f"{seguro}.png"
    return seguro[:80]


async def _resolver_bytes_do_print(
    sessao: SessaoChamada,
) -> tuple[bytes | None, str]:
    """
    Obtém os bytes do print na melhor ordem:
    1) cópia salva na sessão
    2) releitura do attachment da mensagem original (ainda no canal)
    3) download pela URL
    """
    nome = _nome_arquivo_seguro(sessao.print_ems_nome_arquivo)

    if sessao.print_ems_bytes:
        return sessao.print_ems_bytes, nome

    mensagem_original = sessao.print_ems_mensagem
    if mensagem_original is not None:
        anexos = getattr(mensagem_original, "attachments", None) or []
        if anexos:
            try:
                dados = await anexos[0].read()
                if dados:
                    nome = _nome_arquivo_seguro(anexos[0].filename or nome)
                    sessao.print_ems_bytes = dados
                    return dados, nome
            except Exception as erro_anexo:
                registrador.warning(
                    f"⚠️ [chamada] releitura do anexo original falhou: {erro_anexo}"
                )

    if sessao.print_ems_url:
        dados, nome_url = await _baixar_bytes_da_url(sessao.print_ems_url)
        if dados:
            return dados, _nome_arquivo_seguro(nome_url)

    return None, nome


async def _criar_topico_print_ems(
    mensagem: discord.Message,
    canal: discord.abc.Messageable,
    sessao: SessaoChamada,
) -> discord.Thread | None:
    """
    Igual ao tópico de provas (advertências):
    1) cria o tópico ligado ao card
    2) posta o print (arquivo do bot)
    3) espera 2 segundos
    4) arquiva + trava

    O tópico some da lista lateral ao arquivar — isso é normal no Discord.
    Ele continua acessível pelo card (ícone de tópico na mensagem).
    """
    thread: discord.Thread | None = None

    try:
        thread = await mensagem.create_thread(
            name="📁 Print /ems",
            auto_archive_duration=60,
            reason="Anexo da chamada de plantão",
        )
    except discord.HTTPException as erro_thread:
        registrador.warning(
            f"⚠️ [chamada] create_thread via mensagem falhou: {erro_thread}"
        )
        try:
            if isinstance(canal, (discord.TextChannel, discord.ForumChannel)):
                thread = await canal.create_thread(
                    name="📁 Print /ems",
                    message=mensagem,
                    auto_archive_duration=60,
                    reason="Anexo da chamada de plantão",
                )
        except discord.HTTPException as erro_canal:
            registrador.warning(
                f"⚠️ [chamada] create_thread via canal falhou: {erro_canal}"
            )
            thread = None

    if thread is None:
        registrador.warning(
            "⚠️ [chamada] Não foi possível criar o tópico para o print do EMS."
        )
        return None

    # Bytes já devem ter sido baixados quando o doutor enviou o print
    dados_imagem, nome_arquivo = await _resolver_bytes_do_print(sessao)

    try:
        if dados_imagem:
            buffer = io.BytesIO(dados_imagem)
            buffer.seek(0)
            arquivo = discord.File(fp=buffer, filename=nome_arquivo)
            await thread.send(
                content=(
                    "## 📁 Print do `/ems`\n"
                    "-# Arquivo reenviado pelo bot (cópia permanente neste tópico)."
                ),
                file=arquivo,
            )
            registrador.info(
                f"✅ [chamada] print no tópico "
                f"({len(dados_imagem)} bytes, {nome_arquivo}) thread={thread.id}"
            )
        elif sessao.print_ems_url:
            await thread.send(
                "## 📁 Print do `/ems`\n"
                "-# Não foi possível anexar o arquivo. Link original (pode expirar):\n"
                f"{sessao.print_ems_url}"
            )
        else:
            await thread.send(
                "## 📁 Print do `/ems`\n_Nenhum print foi anexado nesta chamada._"
            )
    except Exception as erro_envio:
        registrador.warning(
            f"⚠️ [chamada] falha ao postar print no tópico: {erro_envio}"
        )
        if sessao.print_ems_url:
            try:
                await thread.send(
                    f"⚠️ Falha ao anexar arquivo. Link original:\n{sessao.print_ems_url}"
                )
            except discord.HTTPException as erro_em_criar_topico_print_ems:
                # Enfeite que falhou: criar o topico com o print do EMS.
                # A acao principal ja tinha dado certo, entao so registro.
                ignorar_falha_cosmetica(
                    erro_em_criar_topico_print_ems,
                    o_que_falhou="criar o topico com o print do EMS",
                )

    # Mesmo fluxo das advertências: 2s e fecha
    await asyncio.sleep(2)
    try:
        await thread.edit(
            archived=True,
            locked=True,
            reason="Fechar tópico do Print EMS",
        )
    except discord.HTTPException as erro_fechar:
        registrador.warning(f"⚠️ [chamada] Falha ao fechar tópico: {erro_fechar}")
        try:
            await thread.edit(archived=True, reason="Fechar tópico do Print EMS")
        except discord.HTTPException as erro_fallback:
            registrador.warning(
                f"⚠️ [chamada] Fallback archived falhou: {erro_fallback}"
            )

    return thread


async def _enviar_log_chamada_canal(
    guild: discord.Guild,
    sessao: SessaoChamada,
    presentes: list[MedicoNaChamada],
    faltantes: list[MedicoNaChamada],
):
    """
    Publica o registro da chamada em dois lugares:

    1) CANAL_CHAMADAS_HP_SUL — registro completo + tópico com print /ems
    2) LOG_CHAMADAS — só o registro (sem anexo / sem tópico)
    """
    canal_registro = guild.get_channel(CANAIS.get("CANAL_CHAMADAS_HP_SUL"))
    canal_log = guild.get_channel(CANAIS.get("LOG_CHAMADAS"))

    total_ausentes_ems = len(sessao.toggle_ligado_mas_nao_no_ems)
    total_norte = len(sessao.medicos_norte)
    total_toggle = sessao.total_toggle_ligado

    linhas_presentes = (
        "\n".join(
            f"`✅` `{medico.id_fivem}` — <@{medico.discord_id}>" for medico in presentes
        )
        or "_(nenhum)_"
    )
    linhas_faltantes = (
        "\n".join(
            f"`❌` `{medico.id_fivem}` — <@{medico.discord_id}>" for medico in faltantes
        )
        or "_(nenhum)_"
    )
    linhas_norte = (
        "\n".join(
            f"`❓` `{entrada['id_fivem']}` — {entrada['nome_ems']}"
            for entrada in sessao.medicos_norte
        )
        or "_(nenhum)_"
    )

    agora_unix = int(datetime.now(timezone.utc).timestamp())
    icon_url = guild.icon.url if guild.icon else None

    # --- Card público (CANAL_CHAMADAS_HP_SUL) ---
    cabecalho_publico = (
        f"`📋` Total no `/ems`: **{sessao.total_medicos_ems}**\n"
        f"**Identificados — Hospital Norte**: **{total_norte}**\n"
        f"`🟢` Toggle ligado (Discord): **{total_toggle}**\n"
        f"`⚠️` Ausentes (não estavam no EMS): **{total_ausentes_ems}**\n"
        f"👨‍⚕️ Responsável pela chamada: <@{sessao.doutor_id}>"
    )

    componentes_publico: list = []
    titulo_publico = f"# 📋 Novo Registro de Chamada Realizada\n{cabecalho_publico}"
    if icon_url:
        componentes_publico.append(
            discord.ui.Section(
                titulo_publico,
                accessory=discord.ui.Thumbnail(icon_url),
            )
        )
    else:
        componentes_publico.append(discord.ui.TextDisplay(titulo_publico))

    componentes_publico.append(
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large)
    )
    componentes_publico.append(
        discord.ui.TextDisplay(
            f"**Médicos:**\n`✅` Presentes: **{len(presentes)}**\n{linhas_presentes}"
        )
    )
    componentes_publico.append(
        discord.ui.TextDisplay(
            f"**Médicos com Falta/Ausência:**\n"
            f"`❌` Ausentes (não respondeu): **{len(faltantes)}**\n"
            f"{linhas_faltantes}"
        )
    )
    if total_norte:
        componentes_publico.append(
            discord.ui.TextDisplay(
                f"**Identificados — Hospital Norte**\n{linhas_norte}"
            )
        )
    # Print NÃO vai no card — só no tópico (padrão provas)
    componentes_publico.append(
        discord.ui.Separator(spacing=discord.SeparatorSpacing.large)
    )
    componentes_publico.append(
        discord.ui.TextDisplay(f"-# {guild.name} • <t:{agora_unix}:f>")
    )

    layout_publico = discord.ui.LayoutView(timeout=None)
    layout_publico.add_item(
        discord.ui.Container(
            *componentes_publico,
            accent_color=discord.Color.blurple(),
        )
    )

    mensagem_registro: discord.Message | None = None
    if canal_registro is not None:
        mensagem_registro = await canal_registro.send(view=layout_publico)
        await _criar_topico_print_ems(
            mensagem_registro,
            canal_registro,
            sessao,
        )
    else:
        registrador.warning("⚠️ [chamada] CANAL_CHAMADAS_HP_SUL não encontrado.")

    # --- Log interno (LOG_CHAMADAS) — só registro, sem print ---
    if canal_log is None:
        return

    cabecalho_log = (
        f"`📋` Total `/ems`: **{sessao.total_medicos_ems}** · "
        f"Norte **{total_norte}** · "
        f"Toggle **{total_toggle}** · "
        f"Ausentes EMS **{total_ausentes_ems}**\n"
        f"`✅` Presentes **{len(presentes)}** · "
        f"`❌` Faltas **{len(faltantes)}**\n"
        f"👨‍⚕️ Responsável: <@{sessao.doutor_id}>"
    )
    if sessao.chamada_id:
        cabecalho_log = f"`#` Chamada banco **{sessao.chamada_id}**\n" + cabecalho_log

    layout_log = discord.ui.LayoutView(timeout=None)
    layout_log.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(
                f"# 📋 Registro de Chamada Realizada\n{cabecalho_log}"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(
                f"**Presentes:**\n{linhas_presentes}\n\n**Faltas:**\n{linhas_faltantes}"
            ),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(f"-# {guild.name} • <t:{agora_unix}:f>"),
            accent_color=discord.Color.dark_grey(),
        )
    )
    await canal_log.send(view=layout_log)
