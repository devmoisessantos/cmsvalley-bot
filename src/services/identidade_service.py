from sqlalchemy import select
from src.database.connection import async_session
from src.database.models import EstadoPlantao, Recrutamento  


async def resolver_id_fivem(discord_id: int) -> str | None:
    """Prioridade: 1) id_fivem já salvo em EstadoPlantao (de recrutamento anterior ou modal).
    2) Recrutamento aprovado mais recente. Retorna None se não encontrado em nenhum lugar."""
    async with async_session() as session:
        resultado = await session.execute(
            select(EstadoPlantao.id_fivem).where(EstadoPlantao.discord_id == discord_id)
        )
        id_fivem_salvo = resultado.scalar_one_or_none()
        if id_fivem_salvo:
            return id_fivem_salvo

        resultado_rec = await session.execute(
            select(Recrutamento.id_fivem)
            .where(
                Recrutamento.discord_id_candidato == discord_id,
                Recrutamento.status == "APROVADO",
                Recrutamento.id_fivem.is_not(None),
            )
            .order_by(Recrutamento.id.desc())
            .limit(1)
        )
        return resultado_rec.scalar_one_or_none()

