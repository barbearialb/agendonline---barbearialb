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
    "Tradicional": 15,
    "Social": 18,
    "Degradê": 23,
    "Pezim": 7,
    "Navalhado": 25,
    "Barba": 15,
    "Abordagem de visagismo": 45,
    "Consultoria de visagismo": 65,
}

# Lista de serviços para exibição
lista_servicos = list(servicos.keys())

barbeiros = ["Lucas Borges", "Aluizio"]

# --- Constantes para Estados e Cores ---
ESTADO_DISPONIVEL = "Disponível"
ESTADO_OCUPADO = "Ocupado"
ESTADO_PEZIM_AGENDADO = "Pezim_Agendado" # Novo estado
ESTADO_INDISPONIVEL = "Indisponível" # Para almoço/bloqueio manual

COR_DISPONIVEL = "forestgreen"
COR_OCUPADO = "firebrick"
COR_PEZIM = "darkblue"         # Nova cor
COR_INDISPONIVEL = "orange"

# --- Listas de Serviços para Regras do Pezim ---
PEZIM = "Pezim"
SERVICOS_PERMITIDOS_COM_PEZIM = ["Pezim", "Tradicional", "Barba", "Social"]
SERVICOS_BLOQUEADOS_COM_PEZIM = ["Degradê", "Navalhado", "Abordagem de visagismo", "Consultoria de visagismo"]
SERVICOS_QUE_BLOQUEIAM_HORARIO_SEGUINTE = ["Tradicional", "Social", "Degradê", "Navalhado"] # Para combinar com barba

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

# MODIFICAÇÃO: Função de salvar agendamento mais robusta
def salvar_ou_atualizar_agendamento(data, horario, nome, telefone, servicos_novos, barbeiro, estado_atual, dados_atuais=None):
    """
    Salva um novo agendamento ou atualiza um existente (caso de adicionar a um Pezim).
    Usa transação para garantir atomicidade.
    """
    chave_agendamento = f"{data}_{horario}_{barbeiro}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
    data_obj = datetime.strptime(data, '%d/%m/%Y') # Converter string para datetime

    transaction = db.transaction()

    @firestore.transactional
    def processar_agendamento(transaction):
        doc_snapshot = agendamento_ref.get(transaction=transaction) # Ler DENTRO da transação

        if estado_atual == ESTADO_DISPONIVEL:
            if doc_snapshot.exists:
                 # Segurança extra: Alguém agendou enquanto o usuário preenchia o form
                 raise ValueError("Ops! Este horário foi ocupado enquanto você preenchia. Tente novamente.")
            # Criar novo agendamento
            if len(servicos_novos) == 1 and servicos_novos[0] == PEZIM:
                status_horario = ESTADO_PEZIM_AGENDADO # Status inicial do Pezim
            else:
                status_horario = ESTADO_OCUPADO # Ocupado para outros serviços ou múltiplos

            dados_para_salvar = {
                'nome': nome, 'telefone': telefone, 'servicos': servicos_novos,
                'barbeiro': barbeiro, 'data': data_obj, 'horario': horario,
                'status_horario': status_horario # Salvar o status
            }
            transaction.set(agendamento_ref, dados_para_salvar)
            return dados_para_salvar, None # Retorna os dados salvos

        elif estado_atual == ESTADO_PEZIM_AGENDADO:
            if not doc_snapshot.exists:
                 # Segurança extra: Agendamento Pezim foi cancelado enquanto usuário preenchia
                 raise ValueError("Ops! O agendamento 'Pezim' neste horário foi cancelado. Tente novamente.")

            dados_existentes = doc_snapshot.to_dict()
            servicos_existentes = dados_existentes.get('servicos', [])

            # Adicionar novos serviços (evitando duplicatas se Pezim for selecionado novamente)
            servicos_combinados = list(set(servicos_existentes + servicos_novos))

            # Verificar se os NOVOS serviços adicionados são permitidos
            servicos_realmente_adicionados = [s for s in servicos_novos if s not in servicos_existentes]
            if any(s in SERVICOS_BLOQUEADOS_COM_PEZIM for s in servicos_realmente_adicionados):
                 raise ValueError(f"Com o {PEZIM}, só pode agendar {', '.join(SERVICOS_PERMITIDOS_COM_PEZIM)}")

            # Atualizar o agendamento para Ocupado
            dados_para_atualizar = {
                'servicos': servicos_combinados,
                'status_horario': ESTADO_OCUPADO # Mudar status para ocupado
                # Manter nome, telefone, etc., do agendamento original do Pezim?
                # Ou atualizar com os dados do novo cliente? Decidi manter o original.
                # Se quiser atualizar, descomente abaixo:
                # 'nome': nome,
                # 'telefone': telefone,
            }
            transaction.update(agendamento_ref, dados_para_atualizar)
            # Retorna os dados atualizados e os originais (caso precise do nome/tel original)
            dados_completos_atualizados = {**dados_existentes, **dados_para_atualizar}
            return dados_completos_atualizados, dados_existentes

        elif estado_atual == ESTADO_OCUPADO:
             raise ValueError("Horário já ocupado.")
        elif estado_atual == ESTADO_INDISPONIVEL:
             raise ValueError("Horário indisponível (almoço ou bloqueado).")
        else:
             raise ValueError("Estado do horário desconhecido.")

    try:
        dados_finais, dados_originais_pezim = processar_agendamento(transaction)
        return dados_finais, dados_originais_pezim # Sucesso
    except ValueError as e:
        st.error(f"{e}")
        return None, None
    except Exception as e:
        st.error(f"Erro inesperado ao salvar/atualizar agendamento: {e}")
        return None, None
    
# Função para cancelar agendamento no Firestore
def cancelar_agendamento(data, horario, telefone, barbeiro):
    chave_agendamento = f"{data}_{horario}_{barbeiro}"
    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
    try:
        doc = agendamento_ref.get()
        if doc.exists and doc.to_dict()['telefone'] == telefone:
            agendamento_data = doc.to_dict()
            # Verificar se a data é um objeto datetime antes de formatar
            if isinstance(agendamento_data['data'], datetime):
                agendamento_data['data'] = agendamento_data['data'].date().strftime('%d/%m/%Y')
            elif isinstance(agendamento_data['data'], str):
                # Se for string, tentamos converter para datetime
                try:
                    # Tentar converter de diferentes formatos comuns
                    try:
                        agendamento_data['data'] = datetime.strptime(agendamento_data['data'], '%Y-%m-%d').date().strftime('%d/%m/%Y')
                    except ValueError:
                        agendamento_data['data'] = datetime.strptime(agendamento_data['data'], '%d/%m/%Y').date().strftime('%d/%m/%Y')

                except ValueError:
                    st.error("Formato de data inválido no Firestore")
                    return None
            else:
                st.error("Formato de data inválida no Firestore")
                return None

            agendamento_ref.delete()
            return agendamento_data
        else:
            return None
    except Exception as e:
        st.error(f"Erro ao acessar o Firestore: {e}")
        return None

# Nova função para desbloquear o horário seguinte
# Nova função para desbloquear o horário seguinte
def desbloquear_horario(data, horario, barbeiro):
     # (código da função inalterado)
    chave_bloqueio = f"{data}_{horario}_{barbeiro}_BLOQUEADO" # Modificação aqui
    agendamento_ref = db.collection('agendamentos').document(chave_bloqueio)
    try:
        doc = agendamento_ref.get()
        if doc.exists and doc.to_dict().get('nome') == "BLOQUEADO": # Usar .get() para segurança
            print(f"Tentando excluir a chave: {chave_bloqueio}")
            agendamento_ref.delete()
            print(f"Horário {horario} do barbeiro {barbeiro} na data {data} desbloqueado.")
    except Exception as e:
        st.error(f"Erro ao desbloquear horário: {e}")

# MODIFICAÇÃO: Função para obter o estado detalhado do horário
@st.cache_data # CUIDADO: Cache pode não refletir o estado imediatamente após uma ação. Remover se causar problemas.
def obter_estado_horario(data, horario, barbeiro):
    """
    Verifica o estado de um horário específico no Firestore.

    Retorna:
        str: Um dos estados: ESTADO_DISPONIVEL, ESTADO_OCUPADO, ESTADO_PEZIM_AGENDADO, ESTADO_INDISPONIVEL.
        dict: Os dados do agendamento, se houver (útil para atualizações). Retorna None se não houver agendamento.
    """
    if not db:
        st.error("Firestore não inicializado.")
        return ESTADO_INDISPONIVEL, None # Considerar indisponível se DB falhar

    chave_agendamento = f"{data}_{horario}_{barbeiro}"
    chave_bloqueio = f"{data}_{horario}_{barbeiro}_BLOQUEADO" # Para bloqueio de horário seguinte

    agendamento_ref = db.collection('agendamentos').document(chave_agendamento)
    bloqueio_ref = db.collection('agendamentos').document(chave_bloqueio)

    try:
        doc_bloqueio = bloqueio_ref.get()
        if doc_bloqueio.exists:
            return ESTADO_OCUPADO, None # Horário bloqueado explicitamente

        doc_agendamento = agendamento_ref.get()
        if doc_agendamento.exists:
            dados_agendamento = doc_agendamento.to_dict()
            servicos_agendados = dados_agendamento.get('servicos', [])

            # Verificar se é apenas Pezim ou se já está Ocupado
            if len(servicos_agendados) == 1 and servicos_agendados[0] == PEZIM:
                 # MODIFICAÇÃO: Adicionando verificação do campo 'status_horario' se ele existir
                 if dados_agendamento.get('status_horario') == ESTADO_PEZIM_AGENDADO:
                    return ESTADO_PEZIM_AGENDADO, dados_agendamento
                 else:
                     # Se não tem status, mas só tem Pezim, consideramos Pezim Agendado (compatibilidade)
                     # Ou talvez marcar como ocupado se o status não existir? Decidi por Pezim Agendado.
                     return ESTADO_PEZIM_AGENDADO, dados_agendamento
            else:
                # Se tem mais de um serviço, ou um serviço diferente de Pezim, está ocupado
                return ESTADO_OCUPADO, dados_agendamento
        else:
            # Nenhuma das chaves existe, horário está disponível
            return ESTADO_DISPONIVEL, None

    except google.api_core.exceptions.RetryError as e:
        st.error(f"Erro de conexão com o Firestore ao verificar horário: {e}")
        return ESTADO_INDISPONIVEL, None
    except Exception as e:
        st.error(f"Erro inesperado ao verificar disponibilidade: {e}")
        return ESTADO_INDISPONIVEL, None
# Função para verificar disponibilidade do horário seguinte (usada para Barba+Corte)
# @retry.Retry() # Retry pode ser útil, mas pode mascarar problemas temporários
def verificar_disponibilidade_horario_seguinte(data, horario, barbeiro):
    if not db:
        st.error("Firestore não inicializado.")
        return False
    try:
        horario_dt = datetime.strptime(horario, '%H:%M')
        horario_seguinte_dt = horario_dt + timedelta(minutes=30)
        horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')

        # Usa a nova função para checar o estado detalhado
        estado_seguinte, _ = obter_estado_horario(data, horario_seguinte_str, barbeiro)

        # Considera disponível se for DISPONIVEL ou se for PEZIM_AGENDADO
        # (porque podemos sobrescrever um Pezim se necessário, embora essa função seja só pra checar)
        # Mas para Barba+Corte, precisamos que esteja realmente livre.
        return estado_seguinte == ESTADO_DISPONIVEL

    except ValueError:
         st.error(f"Formato de horário inválido: {horario}")
         return False
    # Remover retry daqui e deixar a função principal tratar erros de conexão
    # except google.api_core.exceptions.RetryError as e:
    #     st.error(f"Erro de conexão com o Firestore (horário seguinte): {e}")
    #     return False
    except Exception as e:
        st.error(f"Erro inesperado ao verificar horário seguinte: {e}")
        return False

# Função para bloquear horário para um barbeiro específico
# Função para bloquear horário (usada para Barba+Corte)
def bloquear_horario_seguinte(data, horario_atual, barbeiro):
    if not db:
        st.error("Firestore não inicializado.")
        return False
    try:
        horario_atual_dt = datetime.strptime(horario_atual, '%H:%M')
        horario_seguinte_dt = horario_atual_dt + timedelta(minutes=30)
        horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')

        # Converter data string para objeto datetime para salvar no Firestore
        try:
             data_obj = datetime.strptime(data, '%d/%m/%Y')
        except ValueError:
             st.error("Formato de data inválido ao tentar bloquear horário.")
             return False


        chave_bloqueio = f"{data}_{horario_seguinte_str}_{barbeiro}_BLOQUEADO"
        db.collection('agendamentos').document(chave_bloqueio).set({
            'nome': "BLOQUEADO",
            'telefone': "BLOQUEADO",
            'servicos': ["BLOQUEADO"],
            'barbeiro': barbeiro,
            'data': data_obj, # Salvar como datetime
            'horario': horario_seguinte_str,
            'status_horario': ESTADO_INDISPONIVEL # Adicionar status
        })
        st.info(f"O horário das {horario_seguinte_str} de {barbeiro} foi bloqueado.")
        return True
    except ValueError:
         st.error(f"Formato de horário inválido ao tentar bloquear: {horario_atual}")
         return False
    except Exception as e:
        st.error(f"Erro ao bloquear horário seguinte: {e}")
        return False

# Interface Streamlit
st.title("Barbearia Lucas Borges - Agendamentos")
st.header("Faça seu agendamento ou cancele")
st.image("https://github.com/barbearialb/sistemalb/blob/main/icone.png?raw=true", use_container_width=True)

if 'data_agendamento' not in st.session_state:
    st.session_state.data_agendamento = datetime.today().date()  # Inicializar como objeto date

if 'date_changed' not in st.session_state:
    st.session_state['date_changed'] = False

def handle_date_change():
    st.session_state.data_agendamento = st.session_state.data_input_widget  # Atualizar com o objeto date
    obter_estado_horario.clear()

data_agendamento_obj = st.date_input("Data para visualizar disponibilidade", min_value=datetime.today(), key="data_input_widget", on_change=handle_date_change)
data_para_tabela = data_agendamento_obj.strftime('%d/%m/%Y')  # Formatar o objeto date

# Tabela de Disponibilidade (Renderizada com a data do session state) FORA do formulário
# --- Tabela de Disponibilidade ---
st.subheader("Disponibilidade dos Barbeiros")

# Gerar HTML da tabela (MODIFICADO para usar obter_estado_horario)
html_table = '<table style="font-size: 14px; border-collapse: collapse; width: 100%; border: 1px solid #ddd;"><tr><th style="padding: 8px; border: 1px solid #ddd; background-color: #0e1117; color: white;">Horário</th>'
for barbeiro in barbeiros:
    html_table += f'<th style="padding: 8px; border: 1px solid #ddd; background-color: #0e1117; color: white;">{barbeiro}</th>'
html_table += '</tr>'

data_obj_tabela = data_agendamento_obj
dia_da_semana_tabela = data_obj_tabela.weekday()
horarios_tabela = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]

# Placeholder para a tabela enquanto carrega
table_placeholder = st.empty()
table_placeholder.markdown("Carregando disponibilidade...")

# Construir a tabela
tabela_renderizada = False # Flag para evitar renderização duplicada
try:
    linhas_html = ""
    for horario in horarios_tabela:
        linhas_html += f'<tr><td style="padding: 8px; border: 1px solid #ddd;">{horario}</td>'
        for barbeiro in barbeiros:
            status_texto = ""
            bg_color = ""
            color_text = "white"

            # Obter estado detalhado do horário
            estado_horario, _ = obter_estado_horario(data_para_tabela, horario, barbeiro) # Ignora os dados aqui

            hora_int = int(horario.split(':')[0])
            minuto_int = int(horario.split(':')[1])

            # Lógica de Horário de Almoço (PRECEDE a verificação do Firestore)
            horario_almoco = False
            if dia_da_semana_tabela < 5: # Segunda a Sexta
                # Almoço Aluizio: 11:00 - 11:59
                if barbeiro == "Aluizio" and hora_int == 11:
                    horario_almoco = True
                # Almoço Lucas: 13:00 - 13:59
                elif barbeiro == "Lucas Borges" and hora_int == 13:
                    horario_almoco = True
                 # Horário 12:00 - 12:59: Indisponível para ambos
                elif hora_int == 12:
                    horario_almoco = True

            if horario_almoco:
                status_texto = ESTADO_INDISPONIVEL
                bg_color = COR_INDISPONIVEL
            else:
                # Se não for almoço, verifica o estado do Firestore
                if estado_horario == ESTADO_DISPONIVEL:
                    status_texto = "Disponível"
                    bg_color = COR_DISPONIVEL
                elif estado_horario == ESTADO_PEZIM_AGENDADO:
                    status_texto = "Pezim (Rápido)" # Novo texto
                    bg_color = COR_PEZIM         # Nova cor
                elif estado_horario == ESTADO_OCUPADO:
                    status_texto = "Ocupado"
                    bg_color = COR_OCUPADO
                elif estado_horario == ESTADO_INDISPONIVEL: # Pode ser bloqueio do horário seguinte
                     status_texto = "Indisponível"
                     bg_color = COR_INDISPONIVEL
                else: # Fallback
                    status_texto = "Erro"
                    bg_color = "gray"

            linhas_html += f'<td style="padding: 8px; border: 1px solid #ddd; background-color: {bg_color}; text-align: center; color: {color_text}; height: 30px;">{status_texto}</td>'
        linhas_html += '</tr>'

    html_table += linhas_html
    html_table += '</table>'
    table_placeholder.markdown(html_table, unsafe_allow_html=True)
    tabela_renderizada = True

except Exception as e:
     st.error(f"Erro ao gerar a tabela de disponibilidade: {e}")
     if not tabela_renderizada:
         table_placeholder.markdown("Erro ao carregar disponibilidade.")

# Aba de Agendamento (FORMULÁRIO)
with st.form("agendar_form"):
    st.subheader("Agendar Horário")
    nome = st.text_input("Nome")
    telefone = st.text_input("Telefone")

    # Usar o valor do session state para a data dentro do formulário
    data_agendamento = st.session_state.data_agendamento.strftime('%d/%m/%Y') # Formatar para string aqui

    # Geração da lista de horários completa para agendamento
    horarios_base_agendamento = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]

    barbeiro_selecionado = st.selectbox("Escolha o barbeiro", barbeiros + ["Sem preferência"])

    # Filtrar horários de almoço com base no barbeiro selecionado
    horarios_filtrados = []
    for horario in horarios_base_agendamento:
        horarios_filtrados.append(horario)

    horario_agendamento = st.selectbox("Horário", horarios_filtrados)  # Mantenha esta linha

    servicos_selecionados = st.multiselect("Serviços", lista_servicos)

    # Exibir os preços com o símbolo R$
    servicos_com_preco = {servico: f"R$ {preco}" for servico, preco in servicos.items()}
    st.write("Preços dos serviços:")
    for servico, preco in servicos_com_preco.items():
        st.write(f"{servico}: {preco}")

    submitted = st.form_submit_button("Confirmar Agendamento")

# --- INÍCIO DO BLOCO CORRIGIDO ---
if submitted:
    with st.spinner("Processando agendamento..."):
        # --- 1. Validação Inicial dos Campos ---
        if not (nome and telefone and servicos_selecionados):
            st.error("Por favor, preencha nome, telefone e selecione pelo menos 1 serviço.")
            st.stop() # Interrompe a execução se campos básicos faltarem

        # --- 2. Parsing e Informações de Data/Hora ---
        try:
            # Mantém data_agendamento como string 'dd/mm/yyyy' para consistência com funções
            data_obj_agendamento = datetime.strptime(data_agendamento, '%d/%m/%Y')
            horario_obj_agendamento = datetime.strptime(horario_agendamento, '%H:%M') # Usado para cálculos
            dia_da_semana_agendamento = data_obj_agendamento.weekday() # 0=Segunda, 6=Domingo
            hora_agendamento_int = horario_obj_agendamento.hour
        except ValueError:
            st.error("Formato de data ou horário inválido.")
            st.stop()

        # --- 3. Informações sobre os Serviços Selecionados ---
        servicos_visagismo = ["Abordagem de visagismo", "Consultoria de visagismo"]
        is_visagismo_selecionado = any(s in servicos_selecionados for s in servicos_visagismo)
        # Usa a constante definida anteriormente
        is_combo_corte_barba = "Barba" in servicos_selecionados and any(c in servicos_selecionados for c in SERVICOS_QUE_BLOQUEIAM_HORARIO_SEGUINTE)

        # --- 4. Função Auxiliar para Verificar Almoço ---
        #    (Considerando as regras: Aluizio 11h, Lucas 13h, Ambos 12h, Seg-Sex)
        def is_horario_almoco(dia_semana, hora_int, barbeiro):
            if dia_semana >= 5: # Não há almoço definido para Sáb/Dom
                return False
            if hora_int == 12: # Ambos indisponíveis às 12h
                return True
            if barbeiro == "Aluizio" and hora_int == 11:
                return True
            if barbeiro == "Lucas Borges" and hora_int == 13:
                return True
            return False

        # --- 5. Determinar o Barbeiro Final ---
        barbeiro_final = None # Barbeiro que efetivamente fará o serviço

        if barbeiro_selecionado == "Sem preferência":
            barbeiros_potenciais = []
            # 5.1 Filtra por disponibilidade básica e almoço
            for b in barbeiros:
                estado_atual, _ = obter_estado_horario(data_agendamento, horario_agendamento, b)
                # Permitido se 'Disponível' OU 'Pezim Agendado' (pode adicionar serviço)
                if estado_atual in [ESTADO_DISPONIVEL, ESTADO_PEZIM_AGENDADO]:
                     if not is_horario_almoco(dia_da_semana_agendamento, hora_agendamento_int, b):
                         barbeiros_potenciais.append(b)

            # 5.2 Filtra por capacidade (Visagismo)
            barbeiros_aptos_servico = []
            if is_visagismo_selecionado:
                barbeiros_aptos_servico = [b for b in barbeiros_potenciais if b == "Lucas Borges"]
            else:
                barbeiros_aptos_servico = barbeiros_potenciais # Qualquer um serve se não for visagismo

            # 5.3 Filtra por disponibilidade do próximo horário (se Combo Corte+Barba)
            barbeiros_aptos_horario = []
            if is_combo_corte_barba:
                for b in barbeiros_aptos_servico:
                    if verificar_disponibilidade_horario_seguinte(data_agendamento, horario_agendamento, b):
                        barbeiros_aptos_horario.append(b)
            else:
                barbeiros_aptos_horario = barbeiros_aptos_servico # Não precisa verificar próximo horário

            # 5.4 Seleciona aleatório ou mostra erro
            if not barbeiros_aptos_horario:
                st.error("Nenhum barbeiro disponível para esta combinação de horário/serviços.")
                st.info("Verifique: horário de almoço, restrição de visagismo ou disponibilidade do horário seguinte para combos.")
                st.stop()
            else:
                barbeiro_final = random.choice(barbeiros_aptos_horario)
                st.info(f"Barbeiro '{barbeiro_final}' foi selecionado automaticamente.") # Informa ao usuário

        else: # Barbeiro Específico foi selecionado
            barbeiro_final = barbeiro_selecionado # O barbeiro final é o selecionado

            # 5.5 Verifica Almoço para o barbeiro específico
            if is_horario_almoco(dia_da_semana_agendamento, hora_agendamento_int, barbeiro_final):
                st.error(f"O barbeiro {barbeiro_final} está em horário de almoço ({horario_agendamento}).")
                st.stop()

            # 5.6 Verifica Visagismo para o barbeiro específico
            if is_visagismo_selecionado and barbeiro_final != "Lucas Borges":
                st.error("Apenas Lucas Borges realiza atendimentos de visagismo.")
                st.stop()

            # 5.7 Verifica Disponibilidade Básica (Firestore) para o barbeiro específico
            estado_atual, dados_atuais = obter_estado_horario(data_agendamento, horario_agendamento, barbeiro_final)
            if estado_atual == ESTADO_OCUPADO:
                 st.error(f"O horário {horario_agendamento} já está ocupado para {barbeiro_final}.")
                 st.stop()
            elif estado_atual == ESTADO_INDISPONIVEL: # Pode ser bloqueio ou almoço já pego pela func
                 st.error(f"O horário {horario_agendamento} está indisponível para {barbeiro_final}.")
                 st.stop()
            # Se for ESTADO_DISPONIVEL ou ESTADO_PEZIM_AGENDADO, a função de salvar lidará com isso.

            # 5.8 Verifica Próximo Horário (se Combo) para o barbeiro específico
            if is_combo_corte_barba:
                if not verificar_disponibilidade_horario_seguinte(data_agendamento, horario_agendamento, barbeiro_final):
                    horario_seguinte_str = (horario_obj_agendamento + timedelta(minutes=30)).strftime('%H:%M')
                    st.error(f"{barbeiro_final} não pode fazer Corte+Barba neste horário, pois o horário seguinte ({horario_seguinte_str}) já está ocupado.")
                    st.stop()

        # --- 6. Processamento Final do Agendamento ---
        #    (Se chegou até aqui, barbeiro_final está definido e as validações passaram)
        if barbeiro_final:
            # 6.1 Obter o estado EXATO antes de tentar salvar (a função transacional fará a verificação final)
            estado_final_antes_salvar, dados_atuais_antes_salvar = obter_estado_horario(data_agendamento, horario_agendamento, barbeiro_final)

            # 6.2 Tentar salvar/atualizar usando a função transacional
            #     Passamos o estado que ACABAMOS de verificar para a função de salvar decidir
            #     se cria um novo (se Disponível) ou atualiza (se Pezim Agendado)
            dados_salvos, _ = salvar_ou_atualizar_agendamento(
                data=data_agendamento, # String 'dd/mm/yyyy'
                horario=horario_agendamento, # String 'HH:MM'
                nome=nome,
                telefone=telefone,
                servicos_novos=servicos_selecionados,
                barbeiro=barbeiro_final,
                estado_atual=estado_final_antes_salvar, # Informa o estado atual para a lógica da função
                dados_atuais=dados_atuais_antes_salvar # Pode ser usado pela função se for atualização
            )

            # 6.3 Pós-Salvamento: Bloquear próximo horário, enviar e-mail, feedback
            if dados_salvos:
                bloqueio_necessario = is_combo_corte_barba
                erro_bloqueio = False

                # Tenta bloquear o próximo horário se necessário
                if bloqueio_necessario:
                    if not bloquear_horario_seguinte(data_agendamento, horario_agendamento, barbeiro_final):
                        # O agendamento principal foi salvo, mas o bloqueio falhou!
                        st.warning("Agendamento principal salvo, mas houve um erro ao bloquear o horário seguinte automaticamente. Por favor, contate o suporte ou verifique a agenda.")
                        erro_bloqueio = True # Informa que o bloqueio falhou

                # Monta o resumo com os dados retornados pela função de salvar
                resumo = f"""
                Resumo do Agendamento:
                
                Nome: {dados_salvos.get('nome', 'N/A')}
                Telefone: {dados_salvos.get('telefone', 'N/A')}
                Data: {data_agendamento}
                Horário: {horario_agendamento}
                Barbeiro: {barbeiro_final}
                Serviços: {', '.join(dados_salvos.get('servicos', []))}
                
                """

                # Envia o e-mail de confirmação
                enviar_email("Agendamento Confirmado", resumo)

                # Limpa o cache da função de verificação para forçar a releitura na próxima vez
                obter_estado_horario.clear() # Use a função que você realmente usa para verificar

                # Feedback ao usuário
                st.success("Agendamento confirmado com sucesso!")
                st.markdown("```\n" + resumo + "\n```") # Exibe o resumo formatado

                if bloqueio_necessario and not erro_bloqueio:
                    horario_seguinte_str = (horario_obj_agendamento + timedelta(minutes=30)).strftime('%H:%M')
                    st.info(f"O horário das {horario_seguinte_str} foi bloqueado para {barbeiro_final} devido ao combo Corte+Barba.")

                # Pausa para o usuário ver a mensagem e recarrega a página
                time.sleep(6)
                st.rerun()

            else:
                # A função salvar_ou_atualizar_agendamento já deve ter mostrado um st.error()
                # Apenas uma mensagem genérica caso a função não tenha mostrado erro por algum motivo.
                st.error("Não foi possível concluir o agendamento. Verifique as mensagens de erro acima ou tente novamente.")
                # Não precisa de st.stop() aqui, pois o fluxo normal termina após o 'if submitted'

        else:
             # Esta condição não deveria ser alcançada se a lógica anterior estiver correta
             st.error("Erro inesperado: Não foi possível determinar um barbeiro para o agendamento.")
             # Não precisa de st.stop()

# Aba de Cancelamento
with st.form("cancelar_form"):
    st.subheader("Cancelar Agendamento")
    telefone_cancelar = st.text_input("Telefone para Cancelamento")
    data_cancelar = st.date_input("Data do Agendamento", min_value=datetime.today())

    # Geração da lista de horários completa para cancelamento
    horarios_base_cancelamento = [f"{h:02d}:{m:02d}" for h in range(8, 20) for m in (0, 30)]

    # Criar a lista de horários para o dropdown de cancelamento
    horarios_filtrados_cancelamento = []
    for horario in horarios_base_cancelamento:
        # CORREÇÃO: Adicionar o horário à lista CORRETA
        horarios_filtrados_cancelamento.append(horario)

    # Agora a lista não está mais vazia
    horario_cancelar = st.selectbox("Horário do Agendamento", horarios_filtrados_cancelamento)

    barbeiro_cancelar = st.selectbox("Barbeiro do Agendamento", barbeiros)
    submitted_cancelar = st.form_submit_button("Cancelar Agendamento")
    if submitted_cancelar:
        # Validação básica para garantir que o telefone foi preenchido
        if not telefone_cancelar:
             st.warning("Por favor, informe o telefone utilizado no agendamento.")
        else:
            with st.spinner("Processando cancelamento..."):
                data_cancelar_str = data_cancelar.strftime('%d/%m/%Y')
                # Chama a função que busca e deleta o agendamento no Firestore
                agendamento_cancelado_dados = cancelar_agendamento(
                    data_cancelar_str, horario_cancelar, telefone_cancelar, barbeiro_cancelar
                )

            # Verifica se a função retornou os dados do agendamento cancelado
            if agendamento_cancelado_dados is not None:
                # Formata o resumo usando .get() para segurança caso alguma chave falte
                resumo_cancelamento = f"""
                Agendamento Cancelado:
                
                Nome: {agendamento_cancelado_dados.get('nome', 'N/A')}
                Telefone: {agendamento_cancelado_dados.get('telefone', 'N/A')}
                Data: {agendamento_cancelado_dados.get('data', 'N/A')}
                Horário: {agendamento_cancelado_dados.get('horario', 'N/A')}
                Barbeiro: {agendamento_cancelado_dados.get('barbeiro', 'N/A')}
                Serviços: {', '.join(agendamento_cancelado_dados.get('servicos', []))}
            
                """
                enviar_email("Agendamento Cancelado", resumo_cancelamento)

                # --- IMPORTANTE: Limpar o cache da função correta ---
                # Substitua 'obter_estado_horario' pelo nome exato da função
                # que você usa com @st.cache_data para verificar a disponibilidade.
                try:
                    obter_estado_horario.clear()
                except NameError:
                    st.warning("Não foi possível limpar o cache de 'obter_estado_horario'. Verifique o nome da função.")
                except Exception as e:
                    st.warning(f"Erro ao limpar cache: {e}")


                st.success("Agendamento cancelado com sucesso!")
                # Usar markdown para melhor formatação do resumo
                st.markdown("```\n" + resumo_cancelamento + "\n```")

                # Lógica para desbloquear horário seguinte (com try-except)
                servicos_cancelados = agendamento_cancelado_dados.get('servicos', [])
                # Usa a mesma constante do bloco de agendamento
                if "Barba" in servicos_cancelados and any(corte in servicos_cancelados for corte in SERVICOS_QUE_BLOQUEIAM_HORARIO_SEGUINTE):
                    try:
                        # Usa os dados retornados pela função de cancelamento
                        horario_original_str = agendamento_cancelado_dados['horario']
                        data_original_str = agendamento_cancelado_dados['data']
                        barbeiro_original = agendamento_cancelado_dados['barbeiro']

                        horario_original_dt = datetime.strptime(horario_original_str, '%H:%M')
                        horario_seguinte_dt = horario_original_dt + timedelta(minutes=30)
                        horario_seguinte_str = horario_seguinte_dt.strftime('%H:%M')

                        # Chama a função para desbloquear
                        desbloquear_horario(data_original_str, horario_seguinte_str, barbeiro_original)
                        st.info(f"O horário seguinte ({horario_seguinte_str}) foi desbloqueado, pois o serviço cancelado era um combo.")

                    except KeyError as e:
                         st.warning(f"Não foi possível determinar os dados para desbloquear o horário seguinte (campo faltando: {e}).")
                    except ValueError as e:
                         st.warning(f"Erro ao calcular horário seguinte para desbloqueio: {e}")
                    except Exception as e:
                         st.warning(f"Erro inesperado ao tentar desbloquear horário seguinte: {e}")

                # Pausa e recarrega a página
                time.sleep(6) # Um segundo a mais para garantir a leitura
                st.rerun()
            else:
                # Mensagem de erro mais detalhada
                st.error("Nenhum agendamento encontrado para a combinação exata de telefone, data, horário e barbeiro informada.")
                st.info("Dica: Verifique se todos os dados digitados estão corretos e correspondem exatamente aos do agendamento original.")
