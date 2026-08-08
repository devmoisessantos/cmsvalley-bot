"""Cog /templates — construtor visual de cards Components V2.

Fluxo:
  1. /templates abrir → painel interativo
  2. Editar título, linhas, cor e botões de link
  3. Preview ao vivo (edita a mensagem)
  4. Gerar código Python pronto para colar no projeto
"""

from __future__ import annotations

from dataclasses import (
    dataclass,
    field,
)

import discord
from discord import app_commands
from discord.ext import commands

from src.utils.error_handling import (
    LoggingModalMixin,
    LoggingViewMixin,
)
from src.utils.mensagens import (
    COR_ERRO,
    COR_INFO,
    COR_SUCESSO,
    enviar_card,
)
from src.utils.permissions import apenas_administrador

# ---------------------------------------------------------------------------
# Estado da sessão de edição (por usuário)
# ---------------------------------------------------------------------------


@dataclass
class RascunhoTemplate:
    titulo: str = "Título do card"
    linhas: list[str] = field(default_factory=lambda: ["Primeira linha do conteúdo."])
    cor_nome: str = "info"
    botoes: list[tuple[str, str]] = field(default_factory=list)  # (rótulo, url)

    @property
    def cor(self) -> discord.Color:
        mapa = {
            "info": discord.Color.blurple(),
            "sucesso": discord.Color.green(),
            "aviso": discord.Color.orange(),
            "erro": discord.Color.red(),
            "escuro": discord.Color.dark_red(),
        }
        return mapa.get(self.cor_nome, discord.Color.blurple())


_rascunhos: dict[int, RascunhoTemplate] = {}


def obter_rascunho(usuario_id: int) -> RascunhoTemplate:
    if usuario_id not in _rascunhos:
        _rascunhos[usuario_id] = RascunhoTemplate()
    return _rascunhos[usuario_id]


def limpar_rascunho(usuario_id: int) -> None:
    _rascunhos.pop(usuario_id, None)


def gerar_codigo_python(rascunho: RascunhoTemplate) -> str:
    """Gera snippet em português no padrão do projeto."""
    linhas_repr = ",\n        ".join(repr(linha) for linha in rascunho.linhas)
    botoes_code = ""
    if rascunho.botoes:
        pares = ",\n        ".join(
            f"({rotulo!r}, {url!r})" for rotulo, url in rascunho.botoes
        )
        botoes_code = f"\n    botoes_link=[\n        {pares}\n    ],"

    mapa_cor = {
        "info": "COR_INFO",
        "sucesso": "COR_SUCESSO",
        "aviso": "COR_AVISO",
        "erro": "COR_ERRO",
        "escuro": "COR_PUNICAO",
    }
    nome_cor = mapa_cor.get(rascunho.cor_nome, "COR_INFO")

    return f"""from src.utils.notificacao import (
    {nome_cor},
    enviar_dm_card,
)

# Template gerado pelo /templates
await enviar_dm_card(
    membro_destino,
    titulo={rascunho.titulo!r},
    linhas=[
        {linhas_repr}
    ],
    cor={nome_cor},{botoes_code}
    guilda=guilda,
)
"""


def montar_preview(rascunho: RascunhoTemplate) -> discord.ui.LayoutView:
    """Monta o LayoutView de preview idêntico ao que o jogador veria."""
    componentes: list = [
        discord.ui.TextDisplay(f"# {rascunho.titulo}"),
    ]
    if rascunho.linhas:
        componentes.append(discord.ui.TextDisplay("\n".join(rascunho.linhas)))

    if rascunho.botoes:
        componentes.append(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        linha = discord.ui.ActionRow()
        for rotulo, url in rascunho.botoes[:5]:
            linha.add_item(
                discord.ui.Button(
                    label=rotulo[:80],
                    style=discord.ButtonStyle.link,
                    url=url,
                )
            )
        componentes.append(linha)

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*componentes, accent_color=rascunho.cor))
    return view


# ---------------------------------------------------------------------------
# Painel de edição
# ---------------------------------------------------------------------------


class PainelTemplatesView(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, usuario_id: int):
        super().__init__(timeout=900)
        self.usuario_id = usuario_id
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        rascunho = obter_rascunho(self.usuario_id)

        linhas_txt = "\n".join(f"• {linha}" for linha in rascunho.linhas) or "_vazio_"
        botoes_txt = (
            "\n".join(f"• [{rotulo}]({url})" for rotulo, url in rascunho.botoes)
            or "_nenhum_"
        )

        row1 = discord.ui.ActionRow()
        b_titulo = discord.ui.Button(
            label="Editar título", style=discord.ButtonStyle.primary, emoji="✏️"
        )
        b_titulo.callback = self._cb_titulo
        row1.add_item(b_titulo)

        b_linha = discord.ui.Button(
            label="Adicionar linha", style=discord.ButtonStyle.primary, emoji="➕"
        )
        b_linha.callback = self._cb_adicionar_linha
        row1.add_item(b_linha)

        b_limpar = discord.ui.Button(
            label="Limpar linhas", style=discord.ButtonStyle.secondary, emoji="🧹"
        )
        b_limpar.callback = self._cb_limpar_linhas
        row1.add_item(b_limpar)

        row2 = discord.ui.ActionRow()
        select_cor = discord.ui.Select(
            placeholder="Cor do card…",
            options=[
                discord.SelectOption(label="Info (azul)", value="info"),
                discord.SelectOption(label="Sucesso (verde)", value="sucesso"),
                discord.SelectOption(label="Aviso (laranja)", value="aviso"),
                discord.SelectOption(label="Erro (vermelho)", value="erro"),
                discord.SelectOption(label="Escuro / punição", value="escuro"),
            ],
        )
        select_cor.callback = self._cb_cor
        row2.add_item(select_cor)

        row3 = discord.ui.ActionRow()
        b_botao = discord.ui.Button(
            label="Add botão link", style=discord.ButtonStyle.secondary, emoji="🔗"
        )
        b_botao.callback = self._cb_botao
        row3.add_item(b_botao)

        b_preview = discord.ui.Button(
            label="Preview", style=discord.ButtonStyle.success, emoji="👁️"
        )
        b_preview.callback = self._cb_preview
        row3.add_item(b_preview)

        b_codigo = discord.ui.Button(
            label="Gerar código", style=discord.ButtonStyle.danger, emoji="📄"
        )
        b_codigo.callback = self._cb_codigo
        row3.add_item(b_codigo)

        b_reset = discord.ui.Button(
            label="Resetar", style=discord.ButtonStyle.secondary, emoji="♻️"
        )
        b_reset.callback = self._cb_reset
        row3.add_item(b_reset)

        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay("# 🧩 Construtor de Templates"),
                discord.ui.TextDisplay(
                    "Edite o rascunho, veja o **Preview** e gere o código Python "
                    "no padrão do projeto (`notificacao.py`)."
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                discord.ui.TextDisplay(
                    f"### Título\n`{rascunho.titulo}`\n\n"
                    f"### Linhas\n{linhas_txt}\n\n"
                    f"### Cor\n`{rascunho.cor_nome}`\n\n"
                    f"### Botões link\n{botoes_txt}"
                ),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                row1,
                row2,
                row3,
                accent_color=discord.Color.dark_teal(),
            )
        )

    async def _garantir_dono(self, interacao: discord.Interaction) -> bool:
        if interacao.user.id != self.usuario_id:
            await interacao.response.send_message(
                "❌ Este painel não é seu.", ephemeral=True
            )
            return False
        return True

    async def _atualizar(self, interacao: discord.Interaction):
        self._rebuild()
        if interacao.response.is_done():
            await interacao.edit_original_response(view=self)
        else:
            await interacao.response.edit_message(view=self)

    async def _cb_titulo(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalTitulo(self))

    async def _cb_adicionar_linha(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalLinha(self))

    async def _cb_limpar_linhas(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        obter_rascunho(self.usuario_id).linhas = []
        await self._atualizar(interacao)

    async def _cb_cor(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        valores = interacao.data.get("values") if interacao.data else None
        if valores:
            obter_rascunho(self.usuario_id).cor_nome = valores[0]
        await self._atualizar(interacao)

    async def _cb_botao(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        await interacao.response.send_modal(ModalBotaoLink(self))

    async def _cb_preview(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        rascunho = obter_rascunho(self.usuario_id)
        preview = montar_preview(rascunho)
        await interacao.response.send_message(view=preview, ephemeral=True)

    async def _cb_codigo(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        rascunho = obter_rascunho(self.usuario_id)
        codigo = gerar_codigo_python(rascunho)
        # Discord limita 2000 chars em content
        if len(codigo) > 1900:
            codigo = codigo[:1900] + "\n# …truncado"
        await interacao.response.send_message(
            content=f"```python\n{codigo}\n```",
            ephemeral=True,
        )

    async def _cb_reset(self, interacao: discord.Interaction):
        if not await self._garantir_dono(interacao):
            return
        limpar_rascunho(self.usuario_id)
        obter_rascunho(self.usuario_id)
        await self._atualizar(interacao)


class ModalTitulo(LoggingModalMixin, discord.ui.Modal, title="Editar título"):
    campo = discord.ui.TextInput(
        label="Título",
        max_length=200,
        required=True,
    )

    def __init__(self, painel: PainelTemplatesView):
        super().__init__()
        self.painel = painel
        self.campo.default = obter_rascunho(painel.usuario_id).titulo

    async def on_submit(self, interacao: discord.Interaction):
        obter_rascunho(self.painel.usuario_id).titulo = self.campo.value.strip()
        await self.painel._atualizar(interacao)


class ModalLinha(LoggingModalMixin, discord.ui.Modal, title="Adicionar linha"):
    campo = discord.ui.TextInput(
        label="Texto da linha",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=True,
    )

    def __init__(self, painel: PainelTemplatesView):
        super().__init__()
        self.painel = painel

    async def on_submit(self, interacao: discord.Interaction):
        obter_rascunho(self.painel.usuario_id).linhas.append(self.campo.value.strip())
        await self.painel._atualizar(interacao)


class ModalBotaoLink(LoggingModalMixin, discord.ui.Modal, title="Botão de link"):
    rotulo = discord.ui.TextInput(label="Rótulo do botão", max_length=80, required=True)
    url = discord.ui.TextInput(
        label="URL (https://…)",
        max_length=300,
        required=True,
        placeholder="https://discord.com/channels/…",
    )

    def __init__(self, painel: PainelTemplatesView):
        super().__init__()
        self.painel = painel

    async def on_submit(self, interacao: discord.Interaction):
        url = self.url.value.strip()
        if not url.startswith("http"):
            await interacao.response.send_message(
                "❌ URL precisa começar com http:// ou https://",
                ephemeral=True,
            )
            return
        rascunho = obter_rascunho(self.painel.usuario_id)
        if len(rascunho.botoes) >= 5:
            await interacao.response.send_message(
                "❌ Máximo de 5 botões.", ephemeral=True
            )
            return
        rascunho.botoes.append((self.rotulo.value.strip(), url))
        await self.painel._atualizar(interacao)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class TemplatesCog(commands.Cog):
    """Construtor visual de templates de notificação / cards."""

    grupo = app_commands.Group(
        name="templates",
        description="Construtor visual de templates Components V2",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @grupo.command(
        name="abrir",
        description="Abre o painel interativo de criação de templates",
    )
    @apenas_administrador()
    async def abrir(self, interacao: discord.Interaction):
        obter_rascunho(interacao.user.id)
        await interacao.response.send_message(
            view=PainelTemplatesView(interacao.user.id),
            ephemeral=True,
        )

    @grupo.command(
        name="preview-dm",
        description="Envia o preview do rascunho atual na sua própria DM",
    )
    @apenas_administrador()
    async def preview_dm(self, interacao: discord.Interaction):
        from src.utils.notificacao import enviar_dm_card

        rascunho = obter_rascunho(interacao.user.id)
        await interacao.response.defer(ephemeral=True)
        enviou = await enviar_dm_card(
            interacao.user,
            titulo=rascunho.titulo,
            linhas=rascunho.linhas,
            cor=rascunho.cor,
            botoes_link=rascunho.botoes or None,
            guilda=interacao.guild,
        )
        await enviar_card(
            interacao,
            titulo="✅ Preview na DM" if enviou else "❌ Falha na DM",
            linhas=["Confira sua caixa de mensagens diretas."],
            cor=COR_SUCESSO if enviou else COR_ERRO,
            delay=15,
        )

    @grupo.command(
        name="ajuda",
        description="Como usar o construtor de templates",
    )
    @apenas_administrador()
    async def ajuda(self, interacao: discord.Interaction):
        await enviar_card(
            interacao,
            titulo="🧩 Ajuda · Templates",
            linhas=[
                "`/templates abrir` — painel visual (editar, preview, código).",
                "`/templates preview-dm` — manda o rascunho na sua DM.",
                "O código gerado usa `enviar_dm_card` de `notificacao.py`.",
                "Cole o snippet no serviço/cog onde a notificação deve sair.",
            ],
            cor=COR_INFO,
            delay=40,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(TemplatesCog(bot))
