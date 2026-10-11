"""
As dez perguntas da prova teórica de porte de arma de fogo.

Ficam no código (não no banco) porque o gabarito e o feedback mudam pouco
e precisam ser versionados junto com o restante do domínio laudos.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PerguntaPorte:
    """Uma questão da prova de porte, com gabarito e textos de feedback."""

    numero: int
    enunciado: str
    opcao_a: str
    opcao_b: str
    opcao_c: str
    resposta_correta: str
    feedback_acerto: str
    feedback_erro: str


TOTAL_PERGUNTAS_PORTE = 10
NOTA_MINIMA_PORTE = 7
TAMANHO_BLOCO_PORTE = 5

PERGUNTAS_PORTE: tuple[PerguntaPorte, ...] = (
    PerguntaPorte(
        numero=1,
        enunciado=(
            "Qual a diferença fundamental entre POSSE e PORTE de arma de fogo?"
        ),
        opcao_a=(
            "Posse permite andar com a arma na cintura; Porte permite apenas "
            "ter a arma em casa."
        ),
        opcao_b=(
            "Posse permite manter a arma apenas no interior da residência ou "
            "local de trabalho; Porte permite circular com a arma."
        ),
        opcao_c=(
            "Não há diferença; ambos permitem o uso livre em qualquer local "
            "da cidade."
        ),
        resposta_correta="B",
        feedback_acerto=(
            "Excelente entendimento. A posse restringe a arma ao interior do "
            "lar ou local de trabalho legalmente reconhecido, enquanto o porte "
            "autoriza o deslocamento e a circulação do cidadão portando o "
            "armamento."
        ),
        feedback_erro=(
            "Incorreto. A posse limita o armamento exclusivamente à proteção "
            "do interior do seu lar ou estabelecimento comercial. Circular "
            "armado na rua sem a licença de porte configura o crime de porte "
            "ilegal de arma de fogo."
        ),
    ),
    PerguntaPorte(
        numero=2,
        enunciado=(
            "Você está em um estabelecimento e percebe que o ambiente está "
            "ficando tenso devido a uma briga de terceiros. Qual sua conduta "
            "como portador de arma de fogo?"
        ),
        opcao_a=(
            'Sacar a arma e mantê-la em "fogo baixo" para garantir que ninguém '
            "se aproxime de você."
        ),
        opcao_b=(
            "Retirar-se do local imediatamente e, se necessário, acionar a "
            "polícia via 190."
        ),
        opcao_c=(
            "Intervir na briga usando o cabo da arma para separar os "
            "envolvidos."
        ),
        resposta_correta="B",
        feedback_acerto=(
            "Excelente decisão. Como civil armado, seu objetivo é a autodefesa "
            "e a preservação da vida, não intervir em brigas que não são suas. "
            "Chame a polícia e afaste-se do local de risco."
        ),
        feedback_erro=(
            "Incorreto. Sacar a arma ou usá-la para intervir em brigas de "
            "terceiros gera pânico, é considerado uso abusivo da força e pode "
            "fazer você ser confundido com um agressor pelas forças policiais."
        ),
    ),
    PerguntaPorte(
        numero=3,
        enunciado=(
            "A pé onde você é a vítima, o assaltante joga a arma dele no chão "
            "e levanta as mãos. Você pode atirar?"
        ),
        opcao_a=(
            "Sim, pois ele te ameaçou momentos antes e merece ser punido."
        ),
        opcao_b=(
            "Sim, para garantir que ele não tente pegar a arma do chão "
            "novamente."
        ),
        opcao_c=(
            "Não. Uma vez que a ameaça cessou e o indivíduo se rendeu, o uso "
            "da força letal torna-se crime."
        ),
        resposta_correta="C",
        feedback_acerto=(
            "Perfeito. A legítima defesa cessa imediatamente no momento em "
            "que a agressão ou ameaça é neutralizada."
        ),
        feedback_erro=(
            "Incorreto. A legítima defesa exige moderação e cessa quando a "
            "ameaça acaba. Atirar em uma pessoa desarmada e rendida "
            "descaracteriza a defesa e configura crime de homicídio ou lesão "
            "corporal."
        ),
    ),
    PerguntaPorte(
        numero=4,
        enunciado=(
            "Em caso de furto ou perda da sua arma de fogo, qual o "
            "procedimento obrigatório imediato?"
        ),
        opcao_a=(
            "Comprar outra imediatamente no mercado ilegal para não ficar "
            "desprotegido."
        ),
        opcao_b=(
            "Registrar um Boletim de Ocorrência e informar imediatamente aos "
            "órgãos competentes."
        ),
        opcao_c=(
            "Tentar procurar a arma por conta própria e só avisar a polícia "
            "se não encontrar."
        ),
        resposta_correta="B",
        feedback_acerto=(
            "Conduta correta. A comunicação imediata evita que a arma seja "
            "vinculada a você caso venha a ser utilizada em atividades "
            "criminosas."
        ),
        feedback_erro=(
            "Incorreto. Deixar de comunicar o extravio, furto ou perda de "
            "arma de fogo imediatamente às autoridades é uma negligência "
            "grave, sujeita a penalidades administrativas e criminais."
        ),
    ),
    PerguntaPorte(
        numero=5,
        enunciado=(
            "É permitido emprestar sua arma registrada para um amigo que se "
            "encontra em situação de risco iminente?"
        ),
        opcao_a=(
            "Jamais. O porte e a posse são nominais e intransferíveis; "
            "emprestar a arma é crime."
        ),
        opcao_b=(
            "Sim, desde que ele também tenha o documento de porte em dia."
        ),
        opcao_c=(
            "Sim, mas apenas por um período curto de tempo (máximo 24 horas)."
        ),
        resposta_correta="A",
        feedback_acerto=(
            "Correto. A concessão do registro e do porte é estritamente "
            "pessoal e intransferível."
        ),
        feedback_erro=(
            "Incorreto. O registro da arma é estritamente nominal. Ceder, "
            "emprestar ou transferir a posse da arma a terceiros sem "
            "autorização legal é crime de cessão ilegal de arma de fogo."
        ),
    ),
    PerguntaPorte(
        numero=6,
        enunciado=(
            "Qual o principal objetivo do armamento para um cidadão "
            "autorizado?"
        ),
        opcao_a=(
            "Auxiliar a polícia em operações de alto risco e invasões de "
            "favelas."
        ),
        opcao_b=(
            "Garantir a defesa da vida em situações extremas onde não há "
            "tempo para o socorro policial."
        ),
        opcao_c=(
            "Impor respeito em discussões e evitar que seja assaltado "
            "através da intimidação visual."
        ),
        resposta_correta="B",
        feedback_acerto=(
            "Correto. A arma de fogo do civil serve unicamente como "
            "instrumento de proteção reativa da integridade física contra "
            "ameaças letais."
        ),
        feedback_erro=(
            "Incorreto. A arma do civil não serve para exercer papel "
            "ostensivo de polícia nem para intimidações em discussões. Seu "
            "único propósito é a defesa da vida em situações de risco "
            "iminente."
        ),
    ),
    PerguntaPorte(
        numero=7,
        enunciado=(
            "Se você efetuar um disparo de advertência para o alto em via "
            "pública para assustar um suspeito, você estará:"
        ),
        opcao_a=(
            "Agindo corretamente para espantar criminosos sem ferir ninguém."
        ),
        opcao_b=(
            "Cometendo um crime de disparo de arma de fogo em via pública, "
            "colocando terceiros em risco."
        ),
        opcao_c=(
            "Sendo um cidadão exemplar ao evitar o confronto direto."
        ),
        resposta_correta="B",
        feedback_acerto=(
            "Exato. Disparar para o alto coloca a vida de terceiros em risco "
            "pelo perigo de projéteis vagantes."
        ),
        feedback_erro=(
            "Incorreto. Projetil disparado para o alto retorna com força "
            "letal. O disparo de advertência em via pública gera perigo "
            "comum a terceiros e constitui crime inafiançável."
        ),
    ),
    PerguntaPorte(
        numero=8,
        enunciado=(
            "Você está portando sua arma legalizada e é parado em uma blitz "
            "ou abordagem policial de rotina. Qual deve ser a sua conduta "
            "imediata?"
        ),
        opcao_a=(
            "Descer do veículo rapidamente com a arma na mão para mostrar o "
            "documento e provar que ela é registrada."
        ),
        opcao_b=(
            "Manter as mãos visíveis no volante, informar calmamente ao "
            "oficial que você é portador de arma de fogo e aguardar as "
            "instruções de como proceder."
        ),
        opcao_c=(
            "Tentar esconder a arma embaixo do banco para evitar passar por "
            "uma revista detalhada."
        ),
        resposta_correta="B",
        feedback_acerto=(
            "Excelente conduta. A transparência e o controle de movimentos "
            "garantem a segurança do portador e da guarnição policial."
        ),
        feedback_erro=(
            "Incorreto. Movimentos bruscos ou descer armado do veículo em "
            "abordagens policiais podem gerar interpretações de ameaça e "
            "reação da equipe policial. Mantenha a calma, as mãos visíveis e "
            "informe o oficial verbalmente."
        ),
    ),
    PerguntaPorte(
        numero=9,
        enunciado=(
            "Você está portando sua arma de fogo e decide consumir bebidas "
            "alcoólicas em um evento social ou clube. Qual deve ser a sua "
            "conduta em relação ao armamento?"
        ),
        opcao_a=(
            "Posso continuar portando normalmente, desde que eu não fique "
            "completamente bêbado."
        ),
        opcao_b=(
            "É estritamente proibido portar arma de fogo sob o efeito de "
            "álcool ou substâncias psicoativas. A arma deve ser guardada "
            "previamente em um cofre seguro na residência."
        ),
        opcao_c=(
            "Posso entregar a arma para o segurança do evento segurar "
            "enquanto eu consumo as bebidas."
        ),
        resposta_correta="B",
        feedback_acerto=(
            "Correto. O porte exige pleno estado de lucidez e discernimento; "
            "álcool e armas de fogo são incompatíveis."
        ),
        feedback_erro=(
            "Incorreto. O consumo de substâncias alcoólicas compromete os "
            "reflexos e a capacidade cognitiva. Portar armas sob alteração "
            "química é falta gravíssima e gera apreensão da arma e cassação "
            "da licença."
        ),
    ),
    PerguntaPorte(
        numero=10,
        enunciado=(
            "Você se envolve em uma discussão verbal acalorada na rua por "
            "conta de uma vaga de estacionamento. Você está armado. O que "
            "você NÃO deve fazer sob hipótese alguma?"
        ),
        opcao_a=(
            "Permanecer dentro do veículo, ignorar as provocações e "
            "aguardar o outro indivíduo se afastar."
        ),
        opcao_b=(
            'Levantar a camisa propositalmente ou colocar a mão na cintura '
            'para exibir o cabo da arma ("mão no coldre") com o objetivo de '
            "intimidar ou fazer o outro recuar."
        ),
        opcao_c=(
            "Sair calmamente com o carro e procurar outra vaga para "
            "estacionar em segurança."
        ),
        resposta_correta="B",
        feedback_acerto=(
            "Exato. Utilizar o armamento de forma velada para coagir "
            "terceiros em desacordos banais constitui abuso de direito e "
            "crime de ameaça."
        ),
        feedback_erro=(
            "Incorreto. Exibir a arma ou colocar a mão no coldre durante "
            "discussões normais constitui o crime de ameaça e uso indevido "
            "do porte, provocando o cancelamento imediato do registro."
        ),
    ),
)


def obter_pergunta(numero: int) -> PerguntaPorte | None:
    """Devolve a pergunta pelo número (1 a 10) ou None se inválido."""
    for pergunta in PERGUNTAS_PORTE:
        if pergunta.numero == numero:
            return pergunta
    return None


def texto_da_opcao(pergunta: PerguntaPorte, letra: str) -> str:
    """Texto da alternativa A, B ou C."""
    letra_maiuscula = letra.upper()
    if letra_maiuscula == "A":
        return pergunta.opcao_a
    if letra_maiuscula == "B":
        return pergunta.opcao_b
    if letra_maiuscula == "C":
        return pergunta.opcao_c
    return ""
