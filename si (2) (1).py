import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore, auth
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
import json
import google.api_core.exceptions
import google.api_core.retry as retry
import random
import pandas as pd
import time

st.markdown(
    """
    <style>
        table {
            display: block !important;
            width: fit-content !important; /* Ou tente width: -webkit-fill-available !important; */
        }
        div[data-testid="stForm"] {
            display: block !important;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# Carregar as credenciais do Firebase e-mail a partir do Streamlit secrets
FIREBASE_CREDENTIALS = None
EMAIL = None
SENHA = None

try:
    # Carregar credenciais do Firebase
    firebase_credentials_json = st.secrets["firebase"]["FIREBASE_CREDENTIALS"]
    FIREBASE_CREDENTIALS = json.loads(firebase_credentials_json)

    # Carregar credenciais de e-mail
    EMAIL = st.secrets["email"]["EMAIL_CREDENCIADO"]
    SENHA = st.secrets["email"]["EMAIL_SENHA"]

except KeyError as e:
    st.error(f"Chave ausente no arquivo secrets.toml: {e}")
except json.JSONDecodeError as e:
    st.error(f"Erro ao decodificar as credenciais do Firebase: {e}")
except Exception as e:
    st.error(f"Erro inesperado: {e}")

# Inicializar Firebase com as credenciais
if FIREBASE_CREDENTIALS:
    if not firebase_admin._apps:  # Verifica se o Firebase já foi inicializado
        try:
            cred = credentials.Certificate(FIREBASE_CREDENTIALS)
            firebase_admin.initialize_app(cred)
        except Exception as e:
            st.error(f"Erro ao inicializar o Firebase: {e}")


# Obter referência do Firestore
db = firestore.client() if firebase_admin._apps else None

# Dados básicos
# A lista de horários base será gerada dinamicamente na tabela

servicos = {
    "Tradicional",
    "Social",
    "Degradê",
    "Pezim",
    "Navalhado",
    "Barba",
    "Abordagem de visagismo",
    "Consultoria de visagismo",
}

# Lista de serviços para exibição
lista_servicos = servicos

barbeiros = ["Lucas Borges", "Aluizio"]

# Função para enviar e-mail
def enviar_email(assunto, mensagem):
    # Proteção extra para caso as credenciais não carreguem
    if not EMAIL or not SENHA:
        st.warning("Credenciais de e-mail não configuradas. E-mail não enviado.")
        return
    try:
        msg = MIMEText(mensagem)
        msg['Subject'] = assunto
        msg['From'] = EMAIL
        msg['To'] = EMAIL

        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(EMAIL, SENHA)  # Login usando as credenciais do e-mail
            server.sendmail(EMAIL, EMAIL, msg.as_string())
    except Exception as e:
        st.error(f"Erro ao enviar e-mail: {e}")

def salvar_agendamento(data, horario, nome, telefone, servicos, barbeiro):
    if not db:
        st.error("Firestore não inicializado. Não é possível salvar.")
        return False # Indicar falha

    chave_agendamento = f"{data}_{horario}_{barbeiro}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)

    # Converter a string de data para um objeto datetime.datetime
    data_obj = datetime.strptime(data, '%d/%m/%Y')

    @firestore.transactional
    def atualizar_agendamento(transaction):
        doc = agendamento_ref.get(transaction=transaction)
        if doc.exists:
            raise ValueError("Horário já ocupado.")
        transaction.set(agendamento_ref, {
            'nome': nome,
            'telefone': telefone,
            'servicos': servicos,
            'barbeiro': barbeiro,
            'data': data_obj,  # Salvar o objeto datetime.datetime no Firestore
            'horario': horario
        })
        return True # Indicar sucesso da transação

    transaction = db.transaction()
    try:
        resultado = atualizar_agendamento(transaction)
        return resultado # Retorna True se sucesso
    except ValueError as e:
        st.error(f"Erro ao salvar agendamento: {e}")
        return False # Indicar falha
    except Exception as e:
        st.error(f"Erro inesperado ao salvar agendamento: {e}")
        return False # Indicar falha

# Função para cancelar agendamento no Firestore
def cancelar_agendamento(data, horario, telefone, barbeiro):
    if not db:
        st.error("Firestore não inicializado. Não é possível cancelar.")
        return None

    chave_agendamento = f"{data}_{horario}_{barbeiro}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
    try:
        doc = agendamento_ref.get()
        if doc.exists and doc.to_dict()['telefone'] == telefone:
            agendamento_data = doc.to_dict()
            # Verificar se a data é um objeto datetime antes de formatar
            if isinstance(agendamento_data.get('data'), datetime): # Usar .get() para segurança
                agendamento_data['data'] = agendamento_data['data'].date().strftime('%d/%m/%Y')
            elif isinstance(agendamento_data.get('data'), str):
                 # Se for string, tentamos converter para datetime
                 try:
                     # Tentar converter de diferentes formatos comuns
                     try:
                         agendamento_data['data'] = datetime.strptime(agendamento_data['data'], '%Y-%m-%d').date().strftime('%d/%m/%Y')
                     except ValueError:
                         agendamento_data['data'] = datetime.strptime(agendamento_data['data'], '%d/%m/%Y').date().strftime('%d/%m/%Y')

                 except ValueError:
                     st.warning("Formato de data inválido no Firestore para este agendamento.")
                     # Pode retornar a string original ou None, dependendo do desejado
                     agendamento_data['data'] = agendamento_data.get('data', 'Data Inválida') # Devolve a string original ou um placeholder
            else:
                 st.warning("Formato de data inválida ou ausente no Firestore para este agendamento.")
                 agendamento_data['data'] = 'Data Inválida' # Define um placeholder

            agendamento_ref.delete()
            return agendamento_data
        else:
            # Se não existe ou telefone não bate, retorna None
            return None
    except Exception as e:
        st.error(f"Erro ao acessar o Firestore durante o cancelamento: {e}")
        return None

# Nova função para desbloquear o horário seguinte
def desbloquear_horario(data, horario, barbeiro):
    if not db:
        st.error("Firestore não inicializado. Não é possível desbloquear.")
        return

    chave_bloqueio = f"{data}_{horario}_{barbeiro}_BLOQUEADO" # Modificação aqui
    agendamento_ref = db.collection('agendamentos').document(chave_bloqueio)
    try:
        doc = agendamento_ref.get()
        if doc.exists and doc.to_dict().get('nome') == "BLOQUEADO": # Usar .get() para segurança
            print(f"Tentando excluir a chave: {chave_bloqueio}")
            agendamento_ref.delete()
            print(f"Horário {horario} do barbeiro {barbeiro} na data {data} desbloqueado.")
        else:
             print(f"Chave de bloqueio não encontrada ou não corresponde a um bloqueio: {chave_bloqueio}")
    except Exception as e:
        st.error(f"Erro ao desbloquear horário: {e}")

# Função para verificar disponibilidade do horário no Firebase
# @st.cache_data # Cache pode causar problemas se a disponibilidade muda rapidamente. Removido.
def verificar_disponibilidade(data, horario, barbeiro=None):
    if not db:
        st.error("Firestore não inicializado.")
        return False # Indisponível se DB não funciona

    # Verificar agendamento regular
    chave_agendamento = f"{data}_{horario}_{barbeiro}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)

    # Verificar horário bloqueado
    chave_bloqueio = f"{data}_{horario}_{barbeiro}_BLOQUEADO"
    bloqueio_ref = db.collection('agendamentos').document(chave_bloqueio)

    try:
        # Tenta obter ambos os documentos
        doc_agendamento = agendamento_ref.get()
        doc_bloqueio = bloqueio_ref.get()
        # Retorna True (disponível) apenas se NENHUM dos dois existir
        return not doc_agendamento.exists and not doc_bloqueio.exists
    except google.api_core.exceptions.RetryError as e:
        st.error(f"Erro de conexão com o Firestore ao verificar disponibilidade: {e}")
        return False # Indisponível em caso de erro de conexão
    except Exception as e:
        st.error(f"Erro inesperado ao verificar disponibilidade: {e}")
        return False # Indisponível em caso de erro inesperado

# Função para verificar disponibilidade do horário e do horário seguinte
# A política de retry padrão do Firestore client geralmente é suficiente.
# @retry.Retry() # Remover se a retry padrão for suficiente
def verificar_disponibilidade_horario_seguinte(data, horario, barbeiro):
    if not db:
        st.error("Firestore não inicializado.")
        return False

    try:
        horario_dt = datetime.strptime(horario, '%H:%M')
        horario_seguinte_dt = horario_dt + timedelta(minutes=30)
        # Verificar se o horário seguinte ainda está no mesmo dia e dentro do expediente
        if horario_seguinte_dt.hour >= 20: # Ex: Se o agendamento é 19:30, não verifica 20:00
            return False # Considera indisponível se ultrapassa o limite

        horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')

        # Verificar agendamento regular no horário seguinte
        chave_agendamento_seguinte = f"{data}_{horario_seguinte_str}_{barbeiro}"
        agendamento_ref_seguinte = db.collection('agendamentos').document(chave_agendamento_seguinte)

        # Verificar bloqueio no horário seguinte
        chave_bloqueio_seguinte = f"{data}_{horario_seguinte_str}_{barbeiro}_BLOQUEADO"
        bloqueio_ref_seguinte = db.collection('agendamentos').document(chave_bloqueio_seguinte)

        doc_agendamento_seguinte = agendamento_ref_seguinte.get()
        doc_bloqueio_seguinte = bloqueio_ref_seguinte.get()

        # Retorna True (disponível) se NENHUM existir no horário seguinte
        return not doc_agendamento_seguinte.exists and not doc_bloqueio_seguinte.exists

    except google.api_core.exceptions.RetryError as e:
        st.error(f"Erro de conexão com o Firestore ao verificar horário seguinte: {e}")
        return False
    except Exception as e:
        st.error(f"Erro inesperado ao verificar disponibilidade do horário seguinte: {e}")
        return False


# Função para bloquear horário para um barbeiro específico
def bloquear_horario(data, horario, barbeiro):
    if not db:
        st.error("Firestore não inicializado. Não é possível bloquear.")
        return False # Indicar falha

    chave_bloqueio = f"{data}_{horario}_{barbeiro}_BLOQUEADO"
    try:
        # Converter a string de data para um objeto datetime.datetime para consistência
        data_obj = datetime.strptime(data, '%d/%m/%Y')
        db.collection('agendamentos').document(chave_bloqueio).set({
            'nome': "BLOQUEADO",
            'telefone': "BLOQUEADO",
            'servicos': ["BLOQUEADO"],
            'barbeiro': barbeiro,
            'data': data_obj, # Salvar como objeto datetime
            'horario': horario
        })
        print(f"Horário {horario} do dia {data} bloqueado para {barbeiro}")
        return True # Indicar sucesso
    except Exception as e:
        st.error(f"Erro ao bloquear horário: {e}")
        return False # Indicar falha

# Interface Streamlit
st.title("Barbearia Lucas Borges - Agendamentos")
st.header("Faça seu agendamento ou cancele")
st.image("https://github.com/barbearialb/sistemalb/blob/main/icone.png?raw=true", use_container_width=True)

# Gerenciamento da Data Selecionada no Session State
if 'data_agendamento' not in st.session_state:
    st.session_state.data_agendamento = datetime.today().date()  # Inicializar como objeto date

if 'date_changed' not in st.session_state:
    st.session_state['date_changed'] = False

def handle_date_change():
    st.session_state.data_agendamento = st.session_state.data_input_widget  # Atualizar com o objeto date
    # verificar_disponibilidade.clear() # Limpar cache se estivesse usando @st.cache_data
    st.session_state['date_changed'] = True # Indica que a data mudou
    # st.rerun() # Força o rerender da página para atualizar a tabela imediatamente (opcional, mas melhora UX)

data_agendamento_obj = st.date_input(
    "Data para visualizar disponibilidade",
    value=st.session_state.data_agendamento, # Usa o valor do session state
    min_value=datetime.today().date(), # Garante que seja um objeto date
    key="data_input_widget",
    on_change=handle_date_change
)

# Atualiza o session state se o valor do widget for diferente (necessário se não usar on_change ou rerun)
if data_agendamento_obj != st.session_state.data_agendamento:
     st.session_state.data_agendamento = data_agendamento_obj
     # verificar_disponibilidade.clear() # Limpar cache se estivesse usando @st.cache_data

# Sempre usa a data do session_state para consistência
data_para_tabela = st.session_state.data_agendamento.strftime('%d/%m/%Y')  # Formatar o objeto date para string DD/MM/YYYY
data_obj_tabela = st.session_state.data_agendamento # Mantém como objeto date para pegar weekday

# Tabela de Disponibilidade (Renderizada com a data do session state) FORA do formulário
st.subheader("Disponibilidade dos Barbeiros")

html_table = '''
<table style="font-size: 14px; border-collapse: collapse; width: 100%; table-layout: fixed; border: 1px solid #ddd;">
<colgroup>
    <col style="width: 20%;">
    <col style="width: 40%;">
    <col style="width: 40%;">
</colgroup>
<tr>
    <th style="padding: 8px; border: 1px solid #ddd; background-color: #0e1117; color: white;">Horário</th>
    <th style="padding: 8px; border: 1px solid #ddd; background-color: #0e1117; color: white;">Lucas Borges</th>
    <th style="padding: 8px; border: 1px solid #ddd; background-color: #0e1117; color: white;">Aluizio</th>
</tr>
'''
for barbeiro in barbeiros:
    html_table += f'<th style="padding: 8px; border: 1px solid #ddd; background-color: #0e1117; color: white;">{barbeiro}</th>'
html_table += '</tr>'

# Gerar horários base dinamicamente
dia_da_semana_tabela = data_obj_tabela.weekday()  # 0 = segunda, 6 = domingo
horarios_tabela = []
for h in range(7, 20):
    for m in (0, 30):
        horario_str = f"{h:02d}:{m:02d}"
        horarios_tabela.append(horario_str)

for horario in horarios_tabela:
    html_table += f'<tr><td style="padding: 8px; border: 1px solid #ddd;">{horario}</td>'
    for barbeiro in barbeiros:
        status = "Indisponível"  # Default
        bg_color = "grey"        # Default
        color_text = "white"     # Default

        hora_int = int(horario.split(':')[0])
        minuto_int = int(horario.split(':')[1])

        # ✅ Esta verificação agora está DENTRO do loop do barbeiro
        if horario in ["07:00", "07:30"]:
            dia_do_mes = data_obj_tabela.day
            mes_do_ano = data_obj_tabela.month
            if not (mes_do_ano == 7 and 11 <= dia_do_mes < 20):
                status = "SDJ"
                bg_color = "#696969"
                color_text = "white"
                html_table += f'<td style="padding: 8px; border: 1px solid #ddd; background-color: {bg_color}; text-align: center; color: {color_text}; height: 30px;">{status}</td>'
                continue

    # >>> REGRA ESPECIAL: Horários 07:00 e 07:30 recebem status "SDJ", exceto de 11 a 20 de julho <<<
     # Pula o restante da lógica para este horário/barbeiro

        # <<< MODIFICAÇÃO 1: Tratamento para Domingo >>>
        #if dia_da_semana_tabela == 6: # Se for Domingo
            #status = "Descanso"
            #bg_color = "#A9A9A9" # Cinza escuro (DarkGray)
            #color_text = "black"
        # <<< FIM MODIFICAÇÃO 1 >>>

        if dia_da_semana_tabela < 5:
            dia = data_obj_tabela.day
            mes = data_obj_tabela.month
            intervalo_especial = mes == 7 and 11 <= dia <= 19

            almoco_lucas = not intervalo_especial and (hora_int == 12 or hora_int == 13)
            almoco_aluizio = not intervalo_especial and (hora_int == 12 or hora_int == 13)
            if barbeiro == "Lucas Borges" and almoco_lucas:
                status = "Almoço"
                bg_color = "orange"
                color_text = "black"
            elif barbeiro == "Aluizio" and almoco_aluizio:
                status = "Almoço"
                bg_color = "orange"
                color_text = "black"
            else:
                disponivel = verificar_disponibilidade(data_para_tabela, horario, barbeiro)
                status = "Disponível" if disponivel else "Ocupado"
                bg_color = "forestgreen" if disponivel else "firebrick"
                color_text = "white"    

        elif dia_da_semana_tabela == 5: # Sábado - Sem almoço, verifica direto
            disponivel = verificar_disponibilidade(data_para_tabela, horario, barbeiro)
            status = "Disponível" if disponivel else "Ocupado"
            bg_color = "forestgreen" if disponivel else "firebrick"
            color_text = "white"

        elif dia_da_semana_tabela == 6:
            dia = data_obj_tabela.day
            mes = data_obj_tabela.month
            if mes == 7 and 11 <= dia <= 19:
                disponivel = verificar_disponibilidade(data_para_tabela, horario, barbeiro)
                status = "Disponível" if disponivel else "Ocupado"
                bg_color = "forestgreen" if disponivel else "firebrick"
                olor_text = "white"
            else:
                status = "Descanso"
                bg_color = "#A9A9A9"
                color_text = "black"
        # Adicionando a célula formatada
        html_table += f'<td style="padding: 8px; border: 1px solid #ddd; background-color: {bg_color}; text-align: center; color: {color_text}; height: 30px;">{status}</td>'

    html_table += '</tr>' # Fecha a linha do horário

html_table += '</table>'
st.markdown(html_table, unsafe_allow_html=True)

# Aba de Agendamento (FORMULÁRIO)
with st.form("agendar_form"):
    st.subheader("Agendar Horário")
    nome = st.text_input("Nome")
    telefone = st.text_input("Telefone")

    # Usar o valor do session state para a data DENTRO do formulário
    # A data exibida aqui será a mesma da tabela, pois ambas usam session_state
    st.write(f"Data selecionada: **{st.session_state.data_agendamento.strftime('%d/%m/%Y')}**")
    data_agendamento_str_form = st.session_state.data_agendamento.strftime('%d/%m/%Y') # String para salvar
    data_obj_agendamento_form = st.session_state.data_agendamento # Objeto date para validações

    # Geração da lista de horários completa para agendamento
    horarios_base_agendamento = [f"{h:02d}:{m:02d}" for h in range(7, 20) for m in (0, 30)]

    barbeiro_selecionado = st.selectbox("Escolha o barbeiro", barbeiros + ["Sem preferência"])

    # Filtrar horários de almoço com base no barbeiro selecionado ou "Sem preferência"
    # (Opcional: Poderia filtrar aqui, mas a validação no submit é mais robusta)
    horarios_disponiveis_dropdown = horarios_base_agendamento # Por enquanto, mostra todos
    # --- Lógica de filtragem complexa poderia entrar aqui ---
    # Mas é mais seguro validar APÓS o submit, pois a disponibilidade pode mudar

    horario_agendamento = st.selectbox("Horário", horarios_disponiveis_dropdown)

    servicos_selecionados = st.multiselect("Serviços", lista_servicos)

    # Exibir os preços com o símbolo R$
    st.write("Serviços disponíveis:")
    for servico in servicos:
        st.write(f"- {servico}")

    submitted = st.form_submit_button("Confirmar Agendamento")

if submitted:
    with st.spinner("Processando agendamento..."):
        # Usar o objeto date diretamente do session state para obter o dia da semana
        dia_da_semana_agendamento = data_obj_agendamento_form.weekday() # 0=Segunda, 6=Domingo
        dia = data_obj_agendamento_form.day
        mes = data_obj_agendamento_form.month
        if dia_da_semana_agendamento == 6:
            if not (mes == 7 and 11 <= dia <= 19):
                st.error("Desculpe, agendamentos aos domingos só são permitidos entre 11 e 19 de julho.")
                st.stop()

        # <<< MODIFICAÇÃO 2: Verificar se é Domingo ANTES de tudo >>>
        #if dia_da_semana_agendamento == 6:
            #st.error("Desculpe, não realizamos agendamentos aos domingos.")
            #st.stop() # Interrompe a execução do agendamento
        # <<< FIM MODIFICAÇÃO 2 >>>

        # Validações básicas de preenchimento
        if not nome or not telefone or not servicos_selecionados:
            st.error("Por favor, preencha seu nome, telefone e selecione pelo menos um serviço.")
            st.stop()
        if horario_agendamento in ["07:00", "07:30"]:
           dia = data_obj_agendamento_form.day
           mes = data_obj_agendamento_form.month
           
           if not (mes == 7 and 11 <= dia <= 20):
            st.error("Os horários de 07:00 e 07:30 só estão disponíveis entre os dias 11 e 19 de julho.")
            st.stop()

        # --- Validações de Horário de Almoço ---
        hora_agendamento_int = int(horario_agendamento.split(':')[0])
        minuto_agendamento_int = int(horario_agendamento.split(':')[1])

        # Verifica almoço apenas se for dia de semana (0 a 4)
        if dia_da_semana_agendamento < 5:
            dia = data_obj_agendamento_form.day
            mes = data_obj_agendamento_form.month
            
            intervalo_especial = mes == 7 and 11 <= dia <= 19
            almoco_lucas = not intervalo_especial and (hora_agendamento_int == 12 or hora_agendamento_int == 13)
            almoco_aluizio = not intervalo_especial and (hora_agendamento_int == 12 or hora_agendamento_int == 13)

            # Se selecionou barbeiro específico
            if barbeiro_selecionado == "Lucas Borges" and almoco_lucas:
                st.error(f"Lucas Borges está em horário de almoço às {horario_agendamento}. Por favor, escolha outro horário.")
                st.stop()
            elif barbeiro_selecionado == "Aluizio" and almoco_aluizio:
                 st.error(f"Aluizio está em horário de almoço às {horario_agendamento}. Por favor, escolha outro horário.")
                 st.stop()
            # Se selecionou "Sem preferência" e AMBOS estão em almoço nesse horário
            # (Nota: Seus horários de almoço não coincidem, então essa condição específica não será atingida,
            # mas a lógica está aqui caso os horários mudem no futuro)
            elif barbeiro_selecionado == "Sem preferência" and almoco_lucas and almoco_aluizio:
                 st.error(f"Ambos os barbeiros estão em horário de almoço às {horario_agendamento}. Por favor, escolha outro horário.")
                 st.stop()
            # Se selecionou "Sem preferência" e o horário coincide com o almoço de UM deles,
            # o sistema tentará agendar com o outro automaticamente mais abaixo. Não precisa parar aqui.

        # --- Validação de Visagismo ---
        servicos_visagismo = ["Abordagem de visagismo", "Consultoria de visagismo"]
        visagismo_selecionado = any(servico in servicos_selecionados for servico in servicos_visagismo)

        if visagismo_selecionado and barbeiro_selecionado == "Aluizio":
             st.error("Apenas Lucas Borges realiza atendimentos de visagismo. Por favor, selecione Lucas Borges ou remova o serviço de visagismo.")
             st.stop()
        # Se selecionou "Sem preferência" e visagismo, força a seleção para Lucas Borges
        if visagismo_selecionado and barbeiro_selecionado == "Sem preferência":
            barbeiro_final = "Lucas Borges"
            st.info("Serviço de visagismo selecionado. Agendamento direcionado para Lucas Borges.")
        elif barbeiro_selecionado != "Sem preferência":
            barbeiro_final = barbeiro_selecionado
        else:
             barbeiro_final = None # Será definido abaixo

        # --- Lógica de Atribuição e Verificação de Disponibilidade ---
        barbeiros_a_verificar = []
        if barbeiro_final: # Se já foi definido (Visagismo ou escolha específica)
            barbeiros_a_verificar.append(barbeiro_final)
        else: # Se for "Sem preferência" e sem visagismo
            barbeiros_a_verificar = list(barbeiros) # Verifica ambos

        barbeiro_agendado = None
        for b in barbeiros_a_verificar:
            # Re-verifica almoço para o caso "Sem preferência"
            if dia_da_semana_agendamento < 5:
                if b == "Lucas Borges" and (hora_agendamento_int == 12): continue # Pula se Lucas está almoçando
                if b == "Aluizio" and (hora_agendamento_int == 11): continue # Pula se Aluizio está almoçando

            # Verifica disponibilidade real no DB
            if verificar_disponibilidade(data_agendamento_str_form, horario_agendamento, b):
                barbeiro_agendado = b
                break # Encontrou um barbeiro disponível

        if not barbeiro_agendado:
            st.error(f"Horário {horario_agendamento} indisponível para os barbeiros selecionados/disponíveis. Por favor, escolha outro horário ou verifique a tabela de disponibilidade.")
            st.stop()

        # --- Verificação de Horário Seguinte para Corte+Barba ---
        precisa_bloquear_proximo = False
        corte_selecionado = any(corte in servicos_selecionados for corte in ["Tradicional", "Social", "Degradê", "Navalhado"])
        barba_selecionada = "Barba" in servicos_selecionados

        if corte_selecionado and barba_selecionada:
            if not verificar_disponibilidade_horario_seguinte(data_agendamento_str_form, horario_agendamento, barbeiro_agendado):
                horario_seguinte_dt = datetime.strptime(horario_agendamento, '%H:%M') + timedelta(minutes=30)
                horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')
                st.error(f"O barbeiro {barbeiro_agendado} não poderá atender para corte e barba, pois já está ocupado no horário seguinte ({horario_seguinte_str}). Por favor, escolha serviços que caibam em 30 minutos ou selecione outro horário/barbeiro.")
                st.stop()
            else:
                precisa_bloquear_proximo = True

        # --- Salvar Agendamento e Bloquear (se necessário) ---
        agendamento_salvo = salvar_agendamento(data_agendamento_str_form, horario_agendamento, nome, telefone, servicos_selecionados, barbeiro_agendado)

        if agendamento_salvo:
            horario_seguinte_bloqueado = False
            if precisa_bloquear_proximo:
                horario_seguinte_dt = datetime.strptime(horario_agendamento, '%H:%M') + timedelta(minutes=30)
                horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')
                horario_seguinte_bloqueado = bloquear_horario(data_agendamento_str_form, horario_seguinte_str, barbeiro_agendado)
                if not horario_seguinte_bloqueado:
                     st.warning("O agendamento principal foi salvo, mas houve um erro ao bloquear o horário seguinte. Por favor, entre em contato com a barbearia se necessário.")


            # --- Preparar e Enviar E-mail ---
            resumo = f"""
            Nome: {nome}
            Telefone: {telefone}
            Data: {data_agendamento_str_form}
            Horário: {horario_agendamento}
            Barbeiro: {barbeiro_agendado}
            Serviços: {', '.join(servicos_selecionados)}
            """
            enviar_email("Agendamento Confirmado", resumo)

            # --- Mensagem de Sucesso e Rerun ---
            st.success("Agendamento confirmado com sucesso!")
            st.info("Resumo do agendamento:\n" + resumo)
            if horario_seguinte_bloqueado:
                st.info(f"O horário das {horario_seguinte_str} com {barbeiro_agendado} foi bloqueado para acomodar todos os serviços.")

            # Limpar cache (se estivesse usando) e atualizar a página
            # verificar_disponibilidade.clear()
            time.sleep(5) # Pausa para o usuário ler as mensagens
            st.rerun()

        else:
            # Mensagem de erro se salvar_agendamento falhar (já exibida pela função)
            st.error("Não foi possível completar o agendamento. Verifique as mensagens de erro acima ou tente novamente.")


# Aba de Cancelamento
with st.form("cancelar_form"):
    st.subheader("Cancelar Agendamento")
    telefone_cancelar = st.text_input("Telefone usado no Agendamento")
    data_cancelar = st.date_input("Data do Agendamento", min_value=datetime.today().date()) # Usar date()

    # Geração da lista de horários completa para cancelamento
    horarios_base_cancelamento = [f"{h:02d}:{m:02d}" for h in range(7, 20) for m in (0, 30)]

    horario_cancelar = st.selectbox("Horário do Agendamento", horarios_base_cancelamento) # Usa a lista completa

    barbeiro_cancelar = st.selectbox("Barbeiro do Agendamento", barbeiros)
    submitted_cancelar = st.form_submit_button("Cancelar Agendamento")

    if submitted_cancelar:
        if not telefone_cancelar:
            st.error("Por favor, informe o telefone utilizado no agendamento.")
        else:
            with st.spinner("Processando cancelamento..."):
                data_cancelar_str = data_cancelar.strftime('%d/%m/%Y') # Formata a data para string DD/MM/YYYY
                agendamento_cancelado_data = cancelar_agendamento(data_cancelar_str, horario_cancelar, telefone_cancelar, barbeiro_cancelar)

            if agendamento_cancelado_data is not None:
                # --- Desbloquear Horário Seguinte (se aplicável) ---
                servicos_cancelados = agendamento_cancelado_data.get('servicos', [])
                corte_no_cancelado = any(corte in servicos_cancelados for corte in ["Tradicional", "Social", "Degradê", "Navalhado"])
                barba_no_cancelado = "Barba" in servicos_cancelados
                horario_seguinte_desbloqueado = False

                if corte_no_cancelado and barba_no_cancelado:
                    horario_agendamento_original = agendamento_cancelado_data['horario']
                    barbeiro_original = agendamento_cancelado_data['barbeiro']
                    data_original_str = agendamento_cancelado_data['data'] # Já deve estar como string DD/MM/YYYY da função cancelar_agendamento

                    horario_seguinte_dt = (datetime.strptime(horario_agendamento_original, '%H:%M') + timedelta(minutes=30))
                    # Verifica se o horário seguinte é válido (antes das 20h)
                    if horario_seguinte_dt.hour < 20:
                        horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')
                        # Tenta desbloquear (a função lida com erros internamente)
                        desbloquear_horario(data_original_str, horario_seguinte_str, barbeiro_original)
                        horario_seguinte_desbloqueado = True # Assume que tentou, a função printa o resultado


                # --- Preparar e Enviar E-mail ---
                # Usa .get() para evitar erros se algum campo não existir no Firestore
                resumo_cancelamento = f"""
                Agendamento Cancelado:
                Nome: {agendamento_cancelado_data.get('nome', 'N/A')}
                Telefone: {agendamento_cancelado_data.get('telefone', 'N/A')}
                Data: {agendamento_cancelado_data.get('data', 'N/A')}
                Horário: {agendamento_cancelado_data.get('horario', 'N/A')}
                Barbeiro: {agendamento_cancelado_data.get('barbeiro', 'N/A')}
                Serviços: {', '.join(agendamento_cancelado_data.get('servicos', []))}
                """
                enviar_email("Agendamento Cancelado", resumo_cancelamento)

                # --- Mensagem de Sucesso e Rerun ---
                # verificar_disponibilidade.clear() # Limpa cache se estivesse usando
                st.success("Agendamento cancelado com sucesso!")
                # st.info("Resumo do cancelamento:\n" + resumo_cancelamento) # Opcional exibir o resumo
                if horario_seguinte_desbloqueado:
                    st.info("O horário seguinte, que estava bloqueado, foi liberado.")
                time.sleep(5)
                st.rerun()
            else:
                # Mensagem se cancelamento falhar (nenhum agendamento encontrado com os dados)
                st.error(f"Não foi encontrado agendamento para o telefone informado na data {data_cancelar_str}, horário {horario_cancelar} e com o barbeiro {barbeiro_cancelar}. Verifique os dados e tente novamente.")
