import discord
from discord import app_commands
from sqlalchemy import select

from src.config import GUILD_ID, CANAIS_PLANTAO, NOMES_CANAIS_PLANTAO
from src.services.plantao_service import ligar_servico, desligar_servico
from src.database.connection import async_session
from src.database.models import EstadoPlantao
from src.utils.error_handling import LoggingViewMixin


class PainelPlantaoLayout(LoggingViewMixin, discord.ui.LayoutView):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        self.guild = guild

        # Botão toggle na linha 0
        self.add_item(self._botao_toggle())

        # Grupos de botões de link
        grupos = [
            [CANAIS_PLANTAO["CALL_INTERNA"], CANAIS_PLANTAO["CALL_EXTERNA"]],
            [CANAIS_PLANTAO["BATE_PAPO_1"], CANAIS_PLANTAO["BATE_PAPO_2"], CANAIS_PLANTAO["BATE_PAPO_3"]],
            CANAIS_PLANTAO["CONSULTORIOS"],           
            CANAIS_PLANTAO["SALA_CURSOS"],         
            [CANAIS_PLANTAO["DIRETORIA"], CANAIS_PLANTAO["DIRETORIA_GERAL"]],
            CANAIS_PLANTAO["RECRUTAMENTO"],      
        ]

        linha_atual = 1
        for grupo in grupos:
            # Verifica limite de 5 botões por linha
            if len(grupo) > 5:
                # Divide em grupos menores se necessário
                for i in range(0, len(grupo), 5):
                    subgrupo = grupo[i:i+5]
                    for canal_id in subgrupo:
                        botao = self._botao_link_canal(canal_id)
                        botao.row = linha_atual
                        self.add_item(botao)
                    linha_atual += 1
            else:
                for canal_id in grupo:
                    botao = self._botao_link_canal(canal_id)
                    botao.row = linha_atual
                    self.add_item(botao)
                linha_atual += 1

    def _botao_toggle(self) -> discord.ui.Button:
        # Inicialmente com estilo padrão - será atualizado depois
        botao = discord.ui.Button(
            label="🔄 Carregando...",
            style=discord.ButtonStyle.secondary,
            custom_id="plantao:toggle",
            row=0,
            disabled=True,  # Desabilitado até carregar estado
        )
        botao.callback = self._callback_toggle
        return botao

    async def _callback_toggle(self, interaction: discord.Interaction):
        # Verifica se é um Member (não User)
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "❌ Este comando só pode ser usado em servidores.", 
                ephemeral=True
            )
            return

        # Verifica permissões (exemplo: apenas cargos específicos)
        if not self._tem_permissao(interaction.user):
            await interaction.response.send_message(
                "❌ Você não tem permissão para usar este botão.", 
                ephemeral=True
            )
            return

        # Defer a resposta para evitar timeout
        await interaction.response.defer(ephemeral=True)

        try:
            # Atualiza o estado
            estado_atual = await self._get_estado_atual(interaction.user.id)
            resultado = await self._alternar(interaction.user)
            
            # Atualiza o botão com o novo estado
            await self._atualizar_botao(interaction, not estado_atual)
            
            await interaction.followup.send(resultado, ephemeral=True)
            
        except Exception as e:
            await interaction.followup.send(
                f"❌ Erro ao alternar serviço: {str(e)}", 
                ephemeral=True
            )

    async def _alternar(self, membro: discord.Member) -> str:
        """Alterna o estado do serviço para o membro."""
        async with async_session() as session:
            resultado = await session.execute(
                select(EstadoPlantao).where(EstadoPlantao.discord_id == membro.id)
            )
            estado = resultado.scalar_one_or_none()
            ja_ligado = estado is not None and estado.toggle_ligado

            if ja_ligado:
                return await desligar_servico(membro)
            else:
                return await ligar_servico(membro)

    async def _get_estado_atual(self, discord_id: int) -> bool:
        """Retorna True se o serviço está ligado."""
        async with async_session() as session:
            resultado = await session.execute(
                select(EstadoPlantao).where(EstadoPlantao.discord_id == discord_id)
            )
            estado = resultado.scalar_one_or_none()
            return estado is not None and estado.toggle_ligado

    async def _atualizar_botao(self, interaction: discord.Interaction, novo_estado: bool):
        """Atualiza o botão toggle com o novo estado."""
        # Procura o botão na view
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.custom_id == "plantao:toggle":
                if novo_estado:
                    item.label = "🔴 Desligar Serviço"
                    item.style = discord.ButtonStyle.danger
                else:
                    item.label = "🟢 Ligar Serviço"
                    item.style = discord.ButtonStyle.success
                item.disabled = False
                await interaction.message.edit(view=self)
                break

    def _tem_permissao(self, membro: discord.Member) -> bool:
        """Verifica se o membro tem permissão para usar o toggle."""
        # Exemplo: apenas cargos com administrador ou cargo específico
        # Ajuste conforme sua necessidade
        if membro.guild_permissions.administrator:
            return True
        
        # Ou verifica cargo específico
        cargo_permitido = discord.utils.get(membro.guild.roles, name="Plantão")  # Ajuste
        return cargo_permitido in membro.roles

    def _botao_link_canal(self, canal_id: int) -> discord.ui.Button:
        """Cria um botão de link para um canal."""
        return discord.ui.Button(
            label=NOMES_CANAIS_PLANTAO.get(canal_id, f"Canal {canal_id}"),
            style=discord.ButtonStyle.link,
            url=f"https://discord.com/channels/{GUILD_ID}/{canal_id}",
        )