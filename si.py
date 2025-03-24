import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore, auth
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
import json
import calendar
import google.api_core.exceptions
import google.api_core.retry as retry


# Carregar as credenciais do Firebase e e-mail a partir do Streamlit secrets
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
horarios = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]
servicos = {
    "Tradicional": 15,
    "Social": 18,
    "Degradê": 23,
    "Navalhado": 25,
    "Pezim": 5,
    "Barba": 15,
}

barbeiros = ["Lucas Borges", "Aluizio", "Sem preferência"]

# Função para enviar e-mail
def enviar_email(assunto, mensagem):
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

# Função para salvar agendamento no Firestore
def salvar_agendamento(data, horario, nome, telefone, servicos, barbeiro):
    chave_agendamento = f"{data}_{horario}"
    db.collection('agendamentos').document(chave_agendamento).set({
        'nome': nome,
        'telefone': telefone,
        'servicos': servicos,
        'barbeiro': barbeiro,
        'data': data,
        'horario': horario
    })

# Função para cancelar agendamento no Firestore
def cancelar_agendamento(data, horario, telefone):
    chave_agendamento = f"{data}_{horario}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
    try:
        doc = agendamento_ref.get()
        if doc.exists and doc.to_dict()['telefone'] == telefone:
            agendamento_cancelado = doc.to_dict()
            agendamento_ref.delete()  # Exclui o agendamento

            # Lógica para remover o bloqueio do próximo horário
            if len(agendamento_cancelado['servicos']) == 2:  # Caso de serviço duplo
                hora, minuto = map(int, horario.split(':'))
                proximo_horario = f"{hora + 1}:{minuto:02d}"
                desbloquear_horario(data, proximo_horario, agendamento_cancelado['barbeiro'])

            return agendamento_cancelado  # Retorna os dados do agendamento cancelado
        else:
            return None
    except Exception as e:
        st.error(f"Erro ao acessar o Firestore: {e}")
        return None
    
# Função para verificar disponibilidade do horário no Firebase
@retry.Retry()
def verificar_disponibilidade(data, horario):
    if not db:
        st.error("Firestore não inicializado.")
        return False  # Retorna False se o Firestore não estiver inicializado
    chave_agendamento = f"{data}_{horario}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
    try:
        doc = agendamento_ref.get()
        if doc.exists:
            st.write(f"Horário {horario} no dia {data} já ocupado.")
        else:
            st.write(f"Horário {horario} no dia {data} disponível.")
        return not doc.exists  # Retorna True se o horário estiver disponível
    except google.api_core.exceptions.RetryError as e:
        st.error(f"Erro de conexão com o Firestore: {e}")
        return False  # Retorna False em caso de erro
    except Exception as e:
        st.error(f"Erro inesperado ao verificar disponibilidade: {e}")
        return False  # Retorna False em caso de erro
    
def filtrar_horarios_disponiveis(data, barbeiro):
    if not db:
        st.error("Firestore não inicializado.")
        return horarios  # Retorna todos os horários se o Firestore não estiver inicializado
    
    try:
        bloqueios_ref = db.collection('bloqueios').where('data', '==', data)
        bloqueios = bloqueios_ref.stream()
        horarios_bloqueados = [doc.to_dict()['horario'] for doc in bloqueios if doc.to_dict().get('barbeiro') == barbeiro]

        # Retornar apenas horários que não estão bloqueados
        horarios_disponiveis = [h for h in horarios if h not in horarios_bloqueados]
        return horarios_disponiveis
    except Exception as e:
        st.error(f"Erro ao carregar bloqueios: {e}")
        return horarios  # Retorna todos os horários em caso de erro


# Função para bloquear horário automaticamente no Firestore
def bloquear_horario(data, horario, barbeiro):
    chave_bloqueio = f"{data}_{horario}_{barbeiro}"
    db.collection('bloqueios').document(chave_bloqueio).set({
        'data': data,
        'horario': horario,
        'barbeiro': barbeiro,
        'timestamp': datetime.now()
    })

def desbloquear_horario(data, horario, barbeiro):
    chave_bloqueio = f"{data}_{horario}_{barbeiro}"
    bloqueio_ref = db.collection('bloqueios').document(chave_bloqueio)
    try:
        bloqueio_ref.delete()
    except Exception as e:
        st.error(f"Erro ao desbloquear horário: {e}")


# Função para filtrar horários disponíveis com base nos bloqueios
def atualizar_cores(data, horario, barbeiro):
    try:
        data_obj = datetime.strptime(data, '%d/%m/%Y').date()
    except ValueError as e:
        st.error(f"Erro ao converter a data: {e}")
        return {"Lucas Borges": "verde", "Aluizio": "verde", "Sem preferência": "verde"}

    try:
        # Consultando agendamentos para o horário e a data
        horarios_ocupados = db.collection('agendamentos').where('data', '==', data).where('horario', '==', horario).stream()
        
        # Verifique se a consulta retornou resultados
        horarios_ocupados_lista = list(horarios_ocupados)  # Converte o resultado em uma lista
        if not horarios_ocupados_lista:
            st.write("Nenhum agendamento encontrado para este horário.")
        
        cores = {"Lucas Borges": "verde", "Aluizio": "verde", "Sem preferência": "verde"}

        for agendamento in horarios_ocupados_lista:
            ag = agendamento.to_dict()  # Verificar se o documento existe antes de processar
            if ag:  # Verifica se o documento foi encontrado e não está vazio
                cores[ag['barbeiro']] = "vermelho"

        if cores["Lucas Borges"] == "vermelho" or cores["Aluizio"] == "vermelho":
            cores["Sem preferência"] = "amarelo"

        # Verificando se o horário está entre 12h e 14h nos dias de semana
        dia_semana = calendar.weekday(data_obj.year, data_obj.month, data_obj.day)
        if dia_semana in range(0, 5) and "12:00" <= horario < "14:00":
            cores["Lucas Borges"] = "vermelho"
            cores["Aluizio"] = "vermelho"
            cores["Sem preferência"] = "vermelho"

        return cores

    except Exception as e:
        st.error(f"Erro ao acessar os dados do Firestore: {e}")
        return {"Lucas Borges": "erro", "Aluizio": "erro", "Sem preferência": "erro"}


# Interface Streamlit
st.title("Barbearia Lucas Borges - Agendamentos")
st.header("Faça seu agendamento ou cancele")
st.image("https://github.com/barbearialb/agendonline---barbearialb/blob/main/icone.png?raw=true", use_container_width=True)

# Aba de Agendamento
st.subheader("Agendar Horário")
nome = st.text_input("Nome")
telefone = st.text_input("Telefone")
data = st.date_input("Data", min_value=datetime.today()).strftime('%d/%m/%Y')
barbeiro = st.selectbox("Escolha o barbeiro", barbeiros)
horarios_disponiveis = filtrar_horarios_disponiveis(data, barbeiro)
horario = st.selectbox("Horário", horarios_disponiveis)
cores = atualizar_cores(data, horario, barbeiro)
st.markdown(f"**Status:** Lucas Borges: {cores['Lucas Borges']}, Aluizio: {cores['Aluizio']}, Sem preferência: {cores['Sem preferência']}")
servicos_selecionados = st.multiselect("Serviços", list(servicos.keys()))

# Exibir os preços com o símbolo R$
servicos_com_preco = {servico: f"R$ {preco}" for servico, preco in servicos.items()}
st.write("Preços dos serviços:")
for servico, preco in servicos_com_preco.items():
    st.write(f"{servico}: {preco}")

# Validação dos serviços selecionados
if st.button("Confirmar Agendamento"):
    if nome and telefone and servicos_selecionados:
        if "Sem preferência" in barbeiro:
            barbeiro = "Sem preferência"

        if len(servicos_selecionados) > 2:
            st.error("Você pode agendar no máximo 2 serviços, sendo o segundo sempre a barba.")
        elif len(servicos_selecionados) == 2 and "Barba" not in servicos_selecionados:
            st.error("Se você escolher dois serviços, o segundo deve ser a barba.")    
        else:
            with st.spinner("Verificando disponibilidade..."):
                if verificar_disponibilidade(data, horario):
                    # Salvar agendamento principal
                    salvar_agendamento(data, horario, nome, telefone, servicos_selecionados, barbeiro)

                    # Caso dois serviços sejam selecionados, bloquear o próximo horário
                    if len(servicos_selecionados) == 2:
                        horario_bloqueado = f"{int(horario.split(':')[0]) + 1}:{horario.split(':')[1]}"
                        db.collection('agendamentos').document(f"{data}_{horario_bloqueado}").set({
                            'barbeiro': barbeiro,
                            'ocupado': True
                        })

                    # Atualizar status dos barbeiros após o agendamento
                    cores = atualizar_cores(data, horario, barbeiro)
                    st.markdown(f"**Status atualizado:** Lucas Borges: {cores['Lucas Borges']}, Aluizio: {cores['Aluizio']}, Sem preferência: {cores['Sem preferência']}")

                    # Resumo do agendamento
                    resumo = f"""
                    Nome: {nome}
                    Telefone: {telefone}
                    Data: {data}
                    Horário: {horario}
                    Barbeiro: {barbeiro}
                    Serviços: {', '.join(servicos_selecionados)}
                    """
                    enviar_email("Agendamento Confirmado", resumo)
                    st.success("Agendamento confirmado com sucesso!")
                    st.info("Resumo do agendamento:\n" + resumo)
                else:
                    st.error("O horário escolhido já está ocupado. Por favor, selecione outro horário.")
    else:
        st.error("Por favor, preencha todos os campos e selecione pelo menos 1 serviço.")


# Aba de Cancelamento
st.subheader("Cancelar Agendamento")
telefone_cancelar = st.text_input("Telefone para Cancelamento")
horario_cancelar = st.selectbox("Horário do Agendamento", horarios)

if st.button("Cancelar Agendamento"):
    with st.spinner("Processando cancelamento..."):
        cancelado = cancelar_agendamento(data, horario_cancelar, telefone_cancelar)
        if cancelado:
            # Atualizar status dos barbeiros após o cancelamento
            cores = atualizar_cores(data, horario_cancelar, cancelado['barbeiro'])
            st.markdown(f"**Status atualizado:** Lucas Borges: {cores['Lucas Borges']}, Aluizio: {cores['Aluizio']}, Sem preferência: {cores['Sem preferência']}")
        
            # Resumo do cancelamento
            resumo_cancelamento = f"""
            Nome: {cancelado['nome']}
            Telefone: {cancelado['telefone']}
            Data: {cancelado['data']}
            Horário: {cancelado['horario']}
            Barbeiro: {cancelado['barbeiro']}
            Serviços: {', '.join(cancelado['servicos'])}
            """
            enviar_email("Agendamento Cancelado", resumo_cancelamento)
            st.success("Agendamento cancelado com sucesso!")
            st.info("Resumo do cancelamento:\n" + resumo_cancelamento)
        else:
            st.error("Não há agendamento para o telefone informado nesse horário.")
