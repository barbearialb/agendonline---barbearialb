import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore, auth
from datetime import datetime, time
import smtplib
from email.mime.text import MIMEText
import json
import calendar
import google.api_core.exceptions
import google.api_core.retry as retry
import random
import time


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

# Dicionário para armazenar agendamentos
agenda = {horario: {"barbeiro": None, "status": "disponível"} for horario in horarios}

# Bloquear horário de almoço (12h - 13h) de segunda a sexta
for horario in ["12:00", "13:30"]:
    agenda[horario]["status"] = "indisponível"

def get_cor_status(status):
    return {
        "disponível": "🟢",
        "indisponível": "🔴",
        "sem preferência": "🟡"
    }[status]

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

cores_iniciais = {"Lucas Borges": "verde", "Aluizio": "verde", "Sem preferência": "verde"}  # Inicialização fora da função

def atualizar_cores(data, horario):
    cores = {"Lucas Borges": "verde", "Aluizio": "verde", "Sem preferência": "verde"}
    try:
        data_obj = datetime.strptime(data, '%d/%m/%Y').date()
        horario_obj = datetime.strptime(horario, '%H:%M').time()
        dia_semana = calendar.weekday(data_obj.year, data_obj.month, data_obj.day)
        horario_minutos = horario_obj.hour * 60 + horario_obj.minute

        # Bloqueio automático apenas para 12:00 até 14:00
        if dia_semana in range(0, 5) and 12 * 60 <= horario_minutos < 14 * 60:
            for barbeiro in barbeiros:
                cores[barbeiro] = "vermelho"
            return cores

        # Consultando Firestore para verificar agendamentos e bloqueios
        agendamentos_ref = db.collection('agendamentos').where('data', '==', data).where('horario', '==', horario)
        agendamentos = list(agendamentos_ref.stream())

        bloqueios_ref = db.collection('bloqueios').where('data', '==', data).where('horario', '==', horario)
        bloqueios = list(bloqueios_ref.stream())

        # Atualizar status conforme Firestore
        for barbeiro in barbeiros:
            if any(ag.to_dict().get('barbeiro') == barbeiro for ag in agendamentos) or any(bl.to_dict().get('barbeiro') == barbeiro for bl in bloqueios):
                cores[barbeiro] = "vermelho"

        # Definição do "Sem preferência"
        if cores["Lucas Borges"] == "verde" and cores["Aluizio"] == "verde":
            cores["Sem preferência"] = "verde"
        elif cores["Lucas Borges"] == "vermelho" and cores["Aluizio"] == "vermelho":
            cores["Sem preferência"] = "vermelho"
        else:
            cores["Sem preferência"] = "amarelo"

        return cores
    except Exception as e:
        st.error(f"Erro ao atualizar cores: {e}")
        return {"Lucas Borges": "erro", "Aluizio": "erro", "Sem preferência": "erro"}
    
@retry.Retry()
def verificar_disponibilidade(data, horario):
    if not db:
        st.error("Firestore não inicializado.")
        return False  # Retorna False se o Firestore não estiver inicializado

    try:
        # Verifica se o horário está dentro do horário de almoço (12h - 14h) em dias de semana
        data_obj = datetime.strptime(data, '%d/%m/%Y').date()
        dia_semana = calendar.weekday(data_obj.year, data_obj.month, data_obj.day)
        if dia_semana in range(0, 5) and "12:00" <= horario < "14:00":
            return False  # Retorna False para bloquear o horário de almoço

        chave_agendamento = f"{data}_{horario}"
        agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
        doc = agendamento_ref.get()
        return not doc.exists  # Retorna True se o horário estiver disponível

    except google.api_core.exceptions.RetryError as e:
        st.error(f"Erro de conexão com o Firestore: {e}")
        return False  # Retorna False em caso de erro
    except Exception as e:
        st.error(f"Erro inesperado ao verificar disponibilidade: {e}")
        return False  # Retorna False em caso de erro
    
# Função para salvar agendamento no Firestore
def salvar_agendamento(data, horario, nome, telefone, servicos, barbeiro):
    if barbeiro == "Sem preferência":
        cores = atualizar_cores(data, horario)
        barbeiros_disponiveis = [b for b, cor in cores.items() if cor == "verde" and b != "Sem preferência"]
        if barbeiros_disponiveis:
            barbeiro = random.choice(barbeiros_disponiveis)
        else:
            st.error("Não há barbeiros disponíveis para este horário.")
            return

    chave_agendamento = f"{data}_{horario}"
    db.collection('agendamentos').document(chave_agendamento).set({
        'nome': nome,
        'telefone': telefone,
        'servicos': servicos,
        'barbeiro': barbeiro,
        'data': data,
        'horario': horario
    })

    # Bloquear o próximo horário apenas se os serviços incluírem "corte" e "barba"
    if "Barba" in servicos and any(corte in servicos for corte in ["Tradicional", "Social", "Degradê", "Navalhado"]):
        hora, minuto = map(int, horario.split(':'))
        proximo_horario = f"{hora + 1}:{minuto:02d}"
        if proximo_horario in horarios: # Verifica se o proximo horário existe
            bloquear_horario(data, proximo_horario, barbeiro) # Linha modificada


# Função para cancelar agendamento no Firestore
def cancelar_agendamento(data, horario, telefone):
    chave_agendamento = f"{data}_{horario}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
    try:
        doc = agendamento_ref.get()
        if doc.exists and doc.to_dict()['telefone'] == telefone:
            agendamento_cancelado = doc.to_dict()
            agendamento_ref.delete()  # Exclui o agendamento

            # Desbloquear o próximo horário apenas se ele tiver sido bloqueado por um agendamento de "corte + barba"
            if "Barba" in agendamento_cancelado['servicos'] and any(corte in agendamento_cancelado['servicos'] for corte in ["Tradicional", "Social", "Degradê", "Navalhado"]):
                hora, minuto = map(int, horario.split(':'))
                proximo_horario = f"{hora + 1}:{minuto:02d}"
                if proximo_horario in horarios: # Verifica se o proximo horário existe
                    desbloquear_horario(data, proximo_horario, agendamento_cancelado['barbeiro']) # Linha modificada

            return agendamento_cancelado  # Retorna os dados do agendamento cancelado
        else:
            return None
    except Exception as e:
        st.error(f"Erro ao acessar o Firestore: {e}")
        return None


# Função para verificar disponibilidade do horário no Firebase


def filtrar_horarios_disponiveis(data, barbeiros):
    st.write(f"🔍 Filtrando horários disponíveis para data: {data}, barbeiros: {barbeiros}")

    if not db:
        st.error("❌ Firestore não inicializado.")
        return []  # Retorna uma lista vazia se Firestore não estiver disponível
    else:
        st.write("✅ Firestore inicializado com sucesso.")

    try:
        # Lista de horários padrão
        horarios = ['08:00', '08:30', '09:00', '09:30', '10:00', '10:30', '11:00', '11:30', 
                    '12:00', '12:30', '13:00', '13:30', '14:00', '14:30', '15:00', '15:30', 
                    '16:00', '16:30', '17:00', '17:30', '18:00', '18:30', '19:00', '19:30']

        # Buscar bloqueios no Firestore
        bloqueios_ref = db.collection('bloqueios')
        bloqueios = list(bloqueios_ref.stream())
        horarios_bloqueados = []

        st.write("📂 Verificando bloqueios no Firestore...")
        for doc in bloqueios:
            bloqueio_dict = doc.to_dict()
            st.write(f"📌 Documento bloqueio analisado: {bloqueio_dict}")

            # Validar se o bloqueio corresponde à data e barbeiros
            if bloqueio_dict.get('data') == data and (
                'Sem preferência' in barbeiros or bloqueio_dict.get('barbeiro') in barbeiros):
                horarios_bloqueados.append(bloqueio_dict.get('horario'))

        # Buscar agendamentos no Firestore
        agendamentos_ref = db.collection('agendamentos')
        agendamentos = list(agendamentos_ref.stream())
        horarios_agendados = []

        st.write("📂 Verificando agendamentos no Firestore...")
        for doc in agendamentos:
            agendamento_dict = doc.to_dict()
            st.write(f"📌 Documento agendamento analisado: {agendamento_dict}")

            # Validar se o agendamento corresponde à data e barbeiros
            if agendamento_dict.get('data') == data and (
                'Sem preferência' in barbeiros or agendamento_dict.get('barbeiro') in barbeiros):
                horarios_agendados.append(agendamento_dict.get('horario'))

        # Logs de horários bloqueados e agendados
        st.write(f"📋 Horários bloqueados: {horarios_bloqueados}")
        st.write(f"📋 Horários agendados: {horarios_agendados}")

        # Combinar horários indisponíveis
        horarios_indisponiveis = set(horarios_bloqueados + horarios_agendados)
        st.write(f"🚫 Horários indisponíveis: {list(horarios_indisponiveis)}")

        # Filtrar horários disponíveis
        horarios_disponiveis = [h for h in horarios if h not in horarios_indisponiveis]
        st.write(f"✅ Horários disponíveis: {horarios_disponiveis}")

        return horarios_disponiveis

    except Exception as e:
        st.error(f"❌ Erro ao carregar horários do Firestore: {e}")
        return []



# Função para bloquear horário automaticamente no Firestore

def bloquear_horario(data, horario, barbeiro):
    if horario not in horarios:
        return  # Caso o próximo horário não exista, sai da função
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

st.title("Barbearia Lucas Borges - Agendamentos")
st.header("Faça seu agendamento ou cancele")
st.image("https://github.com/barbearialb/agendonline---barbearialb/blob/main/icone.png?raw=true",
         use_container_width=True)

# Aba de Agendamento
st.subheader("Agendar Horário")
nome = st.text_input("Nome")
telefone = st.text_input("Telefone")
data = st.date_input("Data", min_value=datetime.today()).strftime('%d/%m/%Y')
barbeiro_escolhido = st.selectbox(" Escolha o barbeiro:", barbeiros)
horarios_disponiveis = filtrar_horarios_disponiveis(data, barbeiros)

# Exibir horários disponíveis com bolinhas coloridas (corrigido)
st.markdown("### Horários Disponíveis:")
for horario in horarios_disponiveis:
    cores = atualizar_cores(data, horario)  # Atualiza as cores para cada horário
    status_str = ""
    for b, cor in cores.items():
        if cor == "verde":
            status_str += f"🟢 {b} "
        elif cor == "amarelo":
            status_str += f"🟡 {b} "
        elif cor == "vermelho":
            status_str += f"🔴 {b} "
        else:
            status_str += f"⚪ {b} (Erro) "
    st.markdown(f"{horario} - {status_str}")

horario = st.selectbox("Selecione o Horário", horarios_disponiveis)

servicos_selecionados = st.multiselect("Serviços", list(servicos.keys()))

# Exibir os preços com o símbolo R$
servicos_com_preco = {servico: f"R$- {preco}" for servico, preco in servicos.items()}
st.write("Preços dos serviços:")
for servico, preco in servicos_com_preco.items():
    st.write(f"{servico}: {preco}")

# Validação dos serviços selecionados
if st.button("Confirmar Agendamento"):  # <--- Mudança aqui
    if nome and telefone and servicos_selecionados:
        if "Sem preferência" in barbeiros:
            # Escolher barbeiro aleatoriamente
            barbeiros_disponiveis = [b for b in barbeiros if b != "Sem preferência" and atualizar_cores(data, horario)[b] == "verde"]
            if barbeiros_disponiveis:
                barbeiro_escolhido = random.choice(barbeiros_disponiveis)
            else:
                st.error("Não há barbeiros disponíveis para este horário. Por favor, escolha outro horário ou barbeiro.")
                st.stop()  # Interrompe a execução do script

            if barbeiro_escolhido != "Sem preferência":
                barbeiro = barbeiro_escolhido

        if len(servicos_selecionados) > 2:
            st.error("Você pode agendar no máximo 2 serviços, sendo o segundo sempre a barba.")
        elif len(servicos_selecionados) == 2 and "Barba" not in servicos_selecionados:
            st.error("Se você escolher dois serviços, o segundo deve ser a barba.")
        else:
            with st.spinner("Verificando disponibilidade e confirmando agendamento..."):
                if verificar_disponibilidade(data, horario):
                    # Salvar agendamento principal
                    salvar_agendamento(data, horario, nome, telefone, servicos_selecionados, barbeiro)

                    # Caso dois serviços sejam selecionados, bloquear o próximo horário
                    if len(servicos_selecionados) == 2:
                        hora, minuto = map(int, horario.split(':'))
                        proximo_horario = f"{hora + 1}:{minuto:02d}"
                        bloquear_horario(data, proximo_horario, barbeiro)

                    time.sleep(1)  # Espera 1 segundo

                    # Atualizar status dos barbeiros após o agendamento
                    cores = atualizar_cores(data, horario)

                    # Exibir status dos barbeiros
                    st.markdown("### Status dos Barbeiros (Atualizado):")
                    for b, cor in cores.items():
                        if cor == "verde":
                            st.markdown(f"🟢 {b}")
                        elif cor == "amarelo":
                            st.markdown(f"🟡 {b}")
                        elif cor == "vermelho":
                            st.markdown(f"🔴 {b}")
                        else:
                            st.markdown(f"⚪ {b} (Erro)")

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
                    st.success("Agendamento confirmado com sucesso! Um e-mail de confirmação foi enviado.")
                    st.info("Resumo do agendamento:\n" + resumo)

                    st.cache_data.clear()  # Limpa o cache
                    st.rerun()
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
            time.sleep(1)  # Espera 1 segundo
            # Atualizar status dos barbeiros após o cancelamento
            cores = atualizar_cores(data, horario_cancelar)
            st.markdown("### Status dos Barbeiros (Atualizado):")
            for b, cor in cores.items():
                if cor == "verde":
                    st.markdown(f"🟢 {b}")
                elif cor == "amarelo":
                    st.markdown(f"🟡 {b}")
                elif cor == "vermelho":
                    st.markdown(f"🔴{b}")
                else:
                    st.markdown(f"⚪ {b} (Erro)")

            # Resumo do cancelamento
            resumo_cancelamento = f"""
            Nome: {cancelado['nome']}
            Telefone: {cancelado['telefone']}
            Data: {cancelado['data']}
            Horário: {cancelado['horario']}
            Barbeiro: {cancelado['barbeiro']}
            Serviços: {', '.join(cancelado['servicos'])}
            """
            st.info("Cancelamento realizado com sucesso!\n" + resumo_cancelamento)
            st.rerun()  # Força a atualização da interface
        else:
            st.error("Agendamento não encontrado ou telefone incorreto.")
    st.cache_data.clear()  # Limpa o cache