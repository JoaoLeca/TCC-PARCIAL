# Ensaio de aceitação no site da versão atual, sem chave da OpenAI – 20/11/2026

> Ensaio executado pelos desenvolvedores com automação do navegador e dados fictícios. **Não** é avaliação com participantes e não substitui a avaliação humana de resolução descrita em [`eval/roteiro_automacao.md`](../../roteiro_automacao.md).

## Configuração

- **Código avaliado:** commit `0c8602a` da `main` (chat do site conduzido pelo agente de IA, avisos e agenda dos barbeiros no Telegram), com o *script* de automação acrescido da variável `AVAL_CENARIOS`, que executa apenas os cenários selecionados.
- **Data e hora:** 20/11/2026, das 10:34 às 10:35 (America/Sao_Paulo).
- **Canal:** site (web). O Telegram não foi avaliado, pois não havia `TELEGRAM_BOT_TOKEN` configurado.
- **IA:** sem `OPENAI_API_KEY`. Nesta versão, o chat não tem respostas locais: sem a chave, toda mensagem livre recebe o aviso de que o atendimento virtual não está habilitado.
- **Cenários executados:** `AVAL_CENARIOS=A01,A05`. Os cenários A02, A03 e A06 a A10 usam a conversa com o agente e dependem da chave; o A04 depende do Telegram. Todos foram registrados como não executados.
- **Janela de reservas:** 1 a 7 dias (21/11 a 27/11/2026). Datas usadas: 21/11 (reserva) e 24/11 (pergunta sobre vagas).
- **Banco:** SQLite de avaliação criado do zero, apenas com a conta de dono; clientes e telefones fictícios.
- **Execução:** [`eval/aceitacao_site.mjs`](../../aceitacao_site.mjs) controla o Chrome sem interface pelo DevTools Protocol e consulta o banco antes e depois de cada passo.

## Resumo

| Cenário | Situação | Verificações aprovadas |
| --- | --- | --- |
| A05 – Conversa livre sem chave OpenAI | **Aprovado** | 3 de 3 |
| A01 – Reserva confirmada (site) | **Aprovado** | 5 de 5 |
| A02, A03, A06, A07, A08, A09 e A10 | **Não executados** (dependem da chave da OpenAI) | – |
| A04 – Botões antigos do Telegram | **Não executado** (depende do *token* do bot) | – |

## Detalhes por cenário

### A05 – Conversa livre sem chave OpenAI (Aprovado)

**Resultado esperado:** sem a chave, toda mensagem livre informa que o atendimento virtual não está habilitado.

**Passos e respostas observadas**

- Cliente: “Quais formas de pagamento vocês aceitam?” → Assistente: “O atendimento virtual não está habilitado.”
- Cliente: “Quanto custa o corte?” → Assistente: “O atendimento virtual não está habilitado.”
- Cliente: “Tem horário para corte dia 24/11?” → Assistente: “O atendimento virtual não está habilitado.”
- Cliente: “Qual a previsão do tempo para amanhã?” → Assistente: “O atendimento virtual não está habilitado.”

**Verificações**

- ✅ Sem a chave, perguntas de atendimento não recebem respostas locais simuladas.
- ✅ Cada pergunta indisponível é registrada na telemetria (`unavailable`).
- ✅ Nenhuma reserva criada.

**Evidência:** [`A05_ia_desativada.png`](evidencias/A05_ia_desativada.png)

### A01 – Reserva confirmada (site) (Aprovado)

**Resultado esperado:** antes da confirmação não existe reserva nova; depois existe exatamente uma, com preço e duração do resumo, e um evento `booking_confirmed`.

**Passos**

- Agendar: serviço Corte de Cabelo, barbeiro Sem preferência, primeiro dia aberto (21/11).
- Horário 15:00 com Caetano; nome e telefone fictícios.
- Resumo: Corte de Cabelo com Caetano, sábado, 21/11/2026 às 15h00 · 40 min · R$ 50,00.

**Verificações**

- ✅ Antes de confirmar não existe reserva nova.
- ✅ Depois da confirmação existe exatamente uma reserva nova e um evento `booking_confirmed`.
- ✅ Reserva #1 gravada com Corte de Cabelo, 15:00, R$ 50,00 e 40 min, como no resumo.
- ✅ A reserva ficou vinculada à sessão do site (`booking_owners`, canal web).
- ✅ Meus horários mostra a reserva na mesma sessão, sem pedir telefone ou código.

A parte do Telegram (`/agendar`) não foi executada, pois depende do *token* do bot.

**Evidências:** [`A01_revisao.png`](evidencias/A01_revisao.png), [`A01_comprovante.png`](evidencias/A01_comprovante.png), [`A01_meus_horarios.png`](evidencias/A01_meus_horarios.png)

## Telemetria

Exportada com `eval/export_logs.py` (sessões pseudonimizadas, sem texto das mensagens) em [`telemetria.csv`](telemetria.csv) e resumida com `eval/metrics.py`:

| Medida | Valor |
| --- | --- |
| Interações registradas | 4, em 1 sessão |
| Respostas de atendimento virtual não habilitado (`unavailable`) | 4 |
| Reservas concluídas | 1 |
| Latência média, mediana e p95 no servidor (n = 4) | 7 ms; 8 ms; 9 ms |

As latências medem apenas o processamento no servidor sem chamada ao modelo e não representam o funcionamento com a IA.

## Pendências

Repetir o roteiro completo com `OPENAI_API_KEY` configurada (cenários A02, A03 e A06 a A10) e, em instância separada sem a chave, o A05. Com o *token* do bot, executar também o A04 e a parte do A01 pelo Telegram.
