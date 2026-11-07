"""Regenera o conjunto rotulado inicial de 150 frases sintéticas."""
import csv
from pathlib import Path

SAMPLES = {
    "saudacao": [
        "Oi, tudo bem?", "Bom dia!", "Boa tarde, pessoal.", "Boa noite, gostaria de uma informação.",
        "Olá, tem alguém aí?", "E aí, beleza?", "Oi, barbearia Imperial.", "Tudo certo por aí?",
        "Boa tarde!", "Olá, bom dia.", "Opa, boa noite.", "Oi, posso tirar uma dúvida?",
        "Alô, tudo bem?", "Saudações, gostaria de falar com a barbearia.", "Olá!",
    ],
    "servicos_precos": [
        "Quanto custa o corte?", "Qual o preço de cabelo e barba?", "Quanto fica pintar o cabelo?",
        "Qual é o valor do corte com sobrancelha?", "Me passa a tabela de preços, por favor.",
        "Quanto custa o pacote de dois cortes no mês?", "O combo cabelo e barba custa quanto?",
        "Quais serviços vocês fazem e quanto cobram?", "Tem pacote mensal? Qual o valor?",
        "Qual o preço para fazer barba e cabelo?", "Quanto fica cabelo, barba e sobrancelha juntos?",
        "O serviço de pintura custa quanto?", "Me informa o valor do corte masculino.",
        "Quanto pago por dois cortes mensais?", "Qual o preço do pacote cabelo mais barba?",
    ],
    "consultar_disponibilidade": [
        "Tem horário livre amanhã?", "Quais horários estão disponíveis para corte?", "O Rafael atende no domingo?",
        "Tem vaga com o Diego na sexta?", "Consigo um horário depois do almoço?", "Que horas o Alecio está livre?",
        "Há disponibilidade para cabelo e barba daqui a dois dias?", "Tem algum barbeiro livre hoje?",
        "Quais horários têm para pintar o cabelo?", "O Diego trabalha na segunda-feira?",
        "Tem vaga às dez da manhã?", "Vocês têm horário no sábado?", "Pode ver um horário livre para mim?",
        "Quero saber a disponibilidade do Rafael amanhã.", "Há algum horário no período da tarde?",
    ],
    "agendar": [
        "Quero marcar um corte para amanhã.", "Pode reservar cabelo e barba para mim?", "Gostaria de agendar uma pintura.",
        "Marca um horário com o Rafael, por favor.", "Quero fazer um agendamento para sexta.",
        "Como faço para reservar um corte?", "Pode me colocar na agenda às dez?", "Quero marcar cabelo e barba no sábado.",
        "Reserve um horário para cortar o cabelo.", "Quero agendar para daqui a dois dias.",
        "Pode marcar comigo e com o Diego?", "Gostaria de reservar um horário para pintar o cabelo.",
        "Agende um corte para mim, por favor.", "Quero fechar um horário amanhã de manhã.",
        "Pode confirmar uma reserva de cabelo e barba?",
    ],
    "remarcar": [
        "Preciso mudar o horário do meu agendamento.", "Pode remarcar meu corte para outro dia?", "Quero trocar minha reserva de horário.",
        "Dá para passar meu horário para sexta?", "Preciso alterar a data do atendimento.", "Quero mudar o horário que marquei.",
        "Remarca meu agendamento para a tarde, por favor.", "Posso transferir minha reserva para sábado?",
        "Queria mudar meu horário com o Rafael.", "Como faço para reagendar meu corte?", "Preciso empurrar o horário para mais tarde.",
        "Altere meu agendamento para amanhã, se possível.", "Quero remarcar cabelo e barba.", "Muda minha reserva para outro horário livre.",
        "Não vou conseguir ir nesse horário; pode reagendar?",
    ],
    "cancelar": [
        "Quero cancelar meu horário.", "Pode desmarcar meu corte?", "Preciso cancelar a reserva de amanhã.",
        "Não vou mais, cancela meu agendamento.", "Como faço para cancelar o horário?", "Pode tirar meu nome da agenda?",
        "Quero desistir da reserva de cabelo e barba.", "Desmarca meu horário com o Diego, por favor.",
        "Preciso cancelar o atendimento que marquei.", "Cancela minha reserva de sexta.", "Não poderei ir, quero cancelar.",
        "Por favor, apague meu horário da agenda.", "Quero desmarcar a pintura de cabelo.",
        "Pode cancelar meu agendamento das dez?", "Preciso liberar o horário que reservei.",
    ],
    "consultar_agendamento": [
        "Qual dia ficou meu corte?", "Você pode verificar minha reserva?", "Tenho algum horário marcado?",
        "Confirma a data do meu agendamento?", "Quero consultar meu horário marcado.", "Com que barbeiro eu marquei?",
        "Pode encontrar minha reserva pelo telefone?", "Quando é meu próximo atendimento?", "Meu agendamento ainda está confirmado?",
        "Me diga os detalhes do horário que reservei.", "Qual serviço eu marquei para sábado?", "Pode consultar minhas reservas?",
        "Confira se tenho um corte agendado.", "Queria confirmar minha marcação de amanhã.", "Em que horário ficou meu atendimento?",
    ],
    "politicas": [
        "Quais formas de pagamento vocês aceitam?", "Até quantas horas antes posso cancelar?", "Qual a tolerância para atraso?",
        "O que acontece se eu faltar?", "Qual é o endereço da barbearia?", "Qual o telefone para contato?",
        "Posso pagar no Pix?", "Com quanto tempo de antecedência posso marcar?", "Quais são os dias de funcionamento?",
        "A barbearia fecha no horário do almoço?", "Aceitam cartão de crédito?", "Como funciona o pacote mensal?",
        "Qual é a regra para atraso?", "Vocês cobram se eu faltar sem avisar?", "Até que horas a barbearia atende?",
    ],
    "atendimento_humano": [
        "Quero falar com uma pessoa.", "Pode me transferir para um atendente?", "Preciso falar com o gerente.",
        "Quero fazer uma reclamação para a equipe.", "Chame alguém da barbearia, por favor.", "Preciso de atendimento humano.",
        "Não quero conversar com robô, me passe para alguém.", "Quero registrar uma reclamação.", "Pode pedir para o barbeiro me ligar?",
        "Preciso de ajuda com um problema no atendimento.", "Quero falar diretamente com um funcionário.",
        "Tem como uma pessoa entrar na conversa?", "Estou insatisfeito e quero falar com o responsável.",
        "Me encaminhe para a equipe, por favor.", "Quero atendimento com uma pessoa de verdade.",
    ],
    "fora_escopo": [
        "Qual a previsão do tempo para amanhã?", "Me ajuda com meu imposto de renda.", "Quais filmes estão no cinema?",
        "Como faço uma receita de bolo?", "Qual é a capital da França?", "Me conte uma piada.",
        "Preciso configurar minha impressora.", "Qual o resultado do jogo de ontem?", "Me explica como investir na bolsa.",
        "Quero comprar uma passagem aérea.", "Como está o trânsito na avenida?", "Pode escrever um poema para mim?",
        "Qual é a cotação do dólar hoje?", "Me ajuda a estudar matemática.", "Quero reservar uma mesa no restaurante.",
    ],
}

if __name__ == "__main__":
    output = Path(__file__).with_name("sentencas_intencao.csv")
    with output.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["sample_id", "text", "true_intent", "predicted_intent"])
        for intent, sentences in SAMPLES.items():
            for index, sentence in enumerate(sentences, 1):
                writer.writerow([f"{intent[:3].upper()}{index:02}", sentence, intent, ""])
    print(f"Criadas {sum(map(len, SAMPLES.values()))} frases em {output}")
