"""
As dez perguntas da prova teórica do Laudo Técnico para Adoção.

Ficam no código (não no banco) porque o gabarito e o feedback mudam pouco
e precisam ser versionados junto com o restante do domínio laudos.

As perguntas usam linguagem de roleplay (cidade, hospital, cidadão)
mantendo a essência de avaliação de prontidão para adoção.

O gabarito mistura A, B e C — não fica sempre na mesma letra.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PerguntaAdocao:
    """Uma questão da prova de adoção, com gabarito e textos de feedback."""

    numero: int
    enunciado: str
    opcao_a: str
    opcao_b: str
    opcao_c: str
    resposta_correta: str
    feedback_acerto: str
    feedback_erro: str


TOTAL_PERGUNTAS_ADOCAO = 10
NOTA_MINIMA_ADOCAO = 7
TAMANHO_BLOCO_ADOCAO = 5


PERGUNTAS_ADOCAO: tuple[PerguntaAdocao, ...] = (
    PerguntaAdocao(
        numero=1,
        enunciado=(
            "No hospital da cidade, qual é a diferença prática entre "
            "adoção e guarda provisória?"
        ),
        opcao_a=(
            "A adoção cria vínculo definitivo; a guarda é temporária e "
            "pode ser revista pela equipe técnica."
        ),
        opcao_b=(
            "As duas são definitivas e transferem a filiação de uma vez."
        ),
        opcao_c=(
            "Não existe diferença; os dois nomes servem para o mesmo "
            "procedimento no sistema da cidade."
        ),
        resposta_correta="A",
        feedback_acerto=(
            "Correto. A adoção fecha o vínculo de forma definitiva. "
            "A guarda é uma etapa de acompanhamento e pode ser ajustada."
        ),
        feedback_erro=(
            "Incorreto. A guarda é temporária e serve para observação. "
            "A adoção é o passo definitivo de filiação."
        ),
    ),
    PerguntaAdocao(
        numero=2,
        enunciado=(
            "Um cidadão quer adotar porque “sempre quis ter alguém em "
            "casa e a gravidez biológica não rolou”. Qual postura a "
            "equipe espera?"
        ),
        opcao_a=(
            "Seguir só com esse desejo, sem precisar olhar outros motivos."
        ),
        opcao_b=(
            "Reconhecer o desejo e também avaliar se a pessoa está pronta "
            "para acolher alguém com história própria na cidade."
        ),
        opcao_c=(
            "Negar a adoção de imediato, pois motivação ligada a "
            "infertilidade nunca é aceita."
        ),
        resposta_correta="B",
        feedback_acerto=(
            "Isso. O desejo é válido, mas a prontidão real para a vida "
            "na cidade com a pessoa adotada também precisa ser olhada."
        ),
        feedback_erro=(
            "O desejo sozinho não basta. A equipe avalia se o cidadão "
            "está preparado para a rotina e os desafios do dia a dia."
        ),
    ),
    PerguntaAdocao(
        numero=3,
        enunciado=(
            "Sobre o passado da pessoa (instituição, abandono ou "
            "situações difíceis na cidade), o adotante deve:"
        ),
        opcao_a=(
            "Evitar qualquer conversa sobre o passado para não "
            "mexer em ferida."
        ),
        opcao_b=(
            "Inventar uma versão mais leve da história para “proteger” "
            "a pessoa."
        ),
        opcao_c=(
            "Respeitar a história e, no momento certo, ajudar a pessoa "
            "a conhecer e integrar esse passado de forma saudável."
        ),
        resposta_correta="C",
        feedback_acerto=(
            "Perfeito. Esconder ou inventar história costuma gerar "
            "problema depois. Respeito e verdade no tempo certo é o caminho."
        ),
        feedback_erro=(
            "Esconder ou inventar o passado costuma voltar como problema. "
            "A postura correta é respeito e acompanhamento no tempo certo."
        ),
    ),
    PerguntaAdocao(
        numero=4,
        enunciado=(
            "Qual atitude mostra melhor preparo emocional para a "
            "adoção dentro da cidade?"
        ),
        opcao_a=(
            "Estar disposto a lidar com rejeição, testes de limite e "
            "períodos difíceis de adaptação na rotina da cidade."
        ),
        opcao_b=(
            "Esperar que a pessoa se adapte rápido e agradeça a "
            "oportunidade."
        ),
        opcao_c=(
            "Usar só presentes e dinheiro para “compensar” o passado "
            "da pessoa."
        ),
        resposta_correta="A",
        feedback_acerto=(
            "Isso. Adaptação leva tempo. Quem espera gratidão imediata "
            "costuma se frustrar e prejudicar o vínculo."
        ),
        feedback_erro=(
            "Expectativa de adaptação rápida ou compensação material "
            "não sustenta o vínculo. O preparo real é lidar com o tempo "
            "e as dificuldades."
        ),
    ),
    PerguntaAdocao(
        numero=5,
        enunciado=(
            "Por que a rede de apoio (amigos, família na cidade, "
            "equipe do hospital) importa no processo de adoção?"
        ),
        opcao_a=(
            "A adoção deve ser um projeto só do adotante, sem "
            "interferência de ninguém."
        ),
        opcao_b=(
            "Só é necessária se a pessoa tiver problema de saúde "
            "ou deficiência."
        ),
        opcao_c=(
            "Cuidar de alguém exige suporte emocional e prático, "
            "principalmente nos primeiros tempos na cidade."
        ),
        resposta_correta="C",
        feedback_acerto=(
            "Correto. Ninguém sustenta sozinho uma mudança tão grande. "
            "Rede de apoio é parte do preparo."
        ),
        feedback_erro=(
            "Isolar o processo aumenta o risco de sobrecarga. A equipe "
            "valoriza quem tem gente para contar nos momentos difíceis."
        ),
    ),
    PerguntaAdocao(
        numero=6,
        enunciado=(
            "Sobre a expectativa de “amor imediato” e vínculo afetivo "
            "depois da adoção:"
        ),
        opcao_a=(
            "O amor costuma nascer no primeiro contato; se não nascer, "
            "a adoção não deve seguir."
        ),
        opcao_b=(
            "O vínculo geralmente se constrói com tempo, rotina e "
            "segurança; não é automático."
        ),
        opcao_c=(
            "Pessoas adotadas não desenvolvem o mesmo tipo de apego "
            "que quem nasceu na família."
        ),
        resposta_correta="B",
        feedback_acerto=(
            "Exato. Vínculo se constrói no dia a dia da cidade, não "
            "em um único momento mágico."
        ),
        feedback_erro=(
            "Esperar amor imediato ou achar que o vínculo nunca será "
            "real gera frustração e prejudica a relação."
        ),
    ),
    PerguntaAdocao(
        numero=7,
        enunciado=(
            "Em relação à estabilidade (casa, renda, rotina na cidade) "
            "para quem quer adotar:"
        ),
        opcao_a=(
            "É preciso condições mínimas de estabilidade para oferecer "
            "segurança e continuidade na cidade."
        ),
        opcao_b=(
            "Basta ter o mínimo; o resto se resolve depois que a "
            "adoção for liberada."
        ),
        opcao_c=(
            "A condição financeira não deve ser olhada, pois isso "
            "seria discriminação."
        ),
        resposta_correta="A",
        feedback_acerto=(
            "Isso. Segurança básica de moradia e rotina faz parte do "
            "cuidado com a pessoa que está sendo acolhida."
        ),
        feedback_erro=(
            "Deixar estabilidade só “para depois” coloca a pessoa em "
            "risco. A equipe avalia condições mínimas de continuidade."
        ),
    ),
    PerguntaAdocao(
        numero=8,
        enunciado=(
            "Uma pessoa em processo de adoção começa a apresentar "
            "comportamentos difíceis (agressividade, mentiras, "
            "isolamento). A resposta mais adequada é:"
        ),
        opcao_a=(
            "Aplicar punição mais dura para “corrigir” o comportamento "
            "rápido."
        ),
        opcao_b=(
            "Devolver a pessoa à instituição, pois isso mostra que "
            "não deu certo."
        ),
        opcao_c=(
            "Tentar entender a origem do comportamento e, se precisar, "
            "pedir apoio da equipe do hospital."
        ),
        resposta_correta="C",
        feedback_acerto=(
            "Correto. Comportamento difícil costuma ter história. "
            "Pedir ajuda técnica é sinal de responsabilidade, não de "
            "fracasso."
        ),
        feedback_erro=(
            "Punição rígida ou devolução imediata não resolvem a "
            "raiz do problema e podem piorar a situação."
        ),
    ),
    PerguntaAdocao(
        numero=9,
        enunciado=(
            "O compromisso da adoção, dentro da lógica da cidade, é:"
        ),
        opcao_a=(
            "Um ato de caridade que pode ser reavaliado se a "
            "convivência ficar difícil."
        ),
        opcao_b=(
            "Um vínculo definitivo, com responsabilidades plenas "
            "de cuidado e proteção."
        ),
        opcao_c=(
            "Um período de experiência de alguns meses, depois do "
            "qual se decide se continua."
        ),
        resposta_correta="B",
        feedback_acerto=(
            "Isso. Adoção não é teste. É compromisso definitivo de "
            "cuidado dentro da cidade."
        ),
        feedback_erro=(
            "Tratar adoção como experiência ou caridade temporária "
            "vai contra o que a equipe técnica espera do adotante."
        ),
    ),
    PerguntaAdocao(
        numero=10,
        enunciado=(
            "Sobre o papel do psicólogo e da equipe técnica no "
            "processo de adoção no hospital:"
        ),
        opcao_a=(
            "O laudo e o acompanhamento servem para proteger a "
            "pessoa e orientar quem está adotando."
        ),
        opcao_b=(
            "O laudo é só uma formalidade para liberar o papel na "
            "prefeitura."
        ),
        opcao_c=(
            "O psicólogo deve só aprovar quem tem boa intenção, "
            "sem olhar condições reais de vida na cidade."
        ),
        resposta_correta="A",
        feedback_acerto=(
            "Perfeito. O trabalho técnico existe para proteger quem "
            "está sendo adotado e preparar quem está adotando."
        ),
        feedback_erro=(
            "Reduzir o laudo a formalidade ou a “boa intenção” "
            "ignora o papel de proteção e orientação da equipe."
        ),
    ),
)


def obter_pergunta(numero: int) -> PerguntaAdocao | None:
    """Devolve a pergunta pelo número (1 a 10) ou None se inválido."""
    if 1 <= numero <= TOTAL_PERGUNTAS_ADOCAO:
        return PERGUNTAS_ADOCAO[numero - 1]
    return None


def texto_da_opcao(pergunta: PerguntaAdocao, letra: str) -> str:
    """Devolve o texto da opção A, B ou C da pergunta."""
    letra_normalizada = (letra or "").strip().upper()
    if letra_normalizada == "A":
        return pergunta.opcao_a
    if letra_normalizada == "B":
        return pergunta.opcao_b
    if letra_normalizada == "C":
        return pergunta.opcao_c
    return "—"
