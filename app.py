import streamlit as st
import pandas as pd
from streamlit_gsheets import GSheetsConnection
from datetime import datetime, timedelta
import time
import os
import io
import cloudinary
import cloudinary.uploader

# -----------------------------------------------------------------------------
# 구글 드라이브 업로드 함수 추가
# -----------------------------------------------------------------------------
# -----------------------------------------------------------------------------
# [Cloudinary] 이미지 업로드 함수
# -----------------------------------------------------------------------------
def upload_to_cloudinary(file_content):
    """Cloudinary에 파일 업로드 후 URL 반환"""
    try:
        # secrets에서 설정 가져오기
        c = st.secrets["cloudinary"]
        cloudinary.config(
            cloud_name = c["cloud_name"],
            api_key = c["api_key"],
            api_secret = c["api_secret"]
        )
        
        # 업로드 실행 (file_content는 bytes)
        response = cloudinary.uploader.upload(file_content)
        return response['secure_url']
        
    except Exception as e:
        st.error(f"이미지 업로드 실패: {e}")
        return None

# -----------------------------------------------------------------------------
# 1. 페이지 설정 및 스타일
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="득근둑근",
    page_icon="💪",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# 모바일 최적화 및 스타일링
st.markdown("""
<style>
    .block-container { padding-top: 1rem; padding-bottom: 2rem; }
    .hof-card {
        background-color: #fce4ec; border-radius: 10px; padding: 15px;
        text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.1); margin-bottom: 10px;
    }
    .hof-rank { font-size: 1.2rem; font-weight: bold; }
    .hof-name { font-size: 1.1rem; font-weight: 600; margin: 3px 0; }
    .hof-score { color: #e91e63; font-weight: bold; font-size: 0.9rem; }
    .stProgress > div > div > div > div { background-image: linear-gradient(to right, #4caf50, #8bc34a); }
</style>
""", unsafe_allow_html=True)


# -----------------------------------------------------------------------------
# 2. 데이터 연결 및 로직 (GSheets)
# -----------------------------------------------------------------------------
conn = st.connection("gsheets", type=GSheetsConnection)

def get_current_week_start():
    """이번 주 시작일 구하기 (일요일 기준)"""
    today = datetime.now()
    # weekday(): 월(0) ~ 일(6)
    # 일요일(6)이면 0일 전, 월요일(0)이면 1일 전... 토요일(5)이면 6일 전
    idx = (today.weekday() + 1) % 7
    start = today - timedelta(days=idx)
    return start.strftime("%Y-%m-%d")

def load_data():
    try:
        df_static = conn.read(worksheet="static_goals", ttl=0)
        df_meas = conn.read(worksheet="measurements", ttl=0)
        df_history = conn.read(worksheet="workout_history", ttl=0)
        
        for df in [df_static, df_meas, df_history]:
            if not df.empty:
                df.columns = [c.strip() for c in df.columns]
                
        # 주간 현황 계산
        if not df_history.empty and '날짜' in df_history.columns:
            week_start = get_current_week_start()
            # 날짜 컬럼을 문자열로 변환 (float/int로 인식되는 경우 방지)
            df_history['날짜'] = df_history['날짜'].astype(str)
            current_week_logs = df_history[df_history['날짜'] >= week_start]
            # [수정] 같은 날 여러 번 인증해도 1회로 카운트 (날짜 중복 제거)
            weekly_counts = current_week_logs.groupby('이름')['날짜'].nunique().reset_index(name='주간현황')
        else:
            weekly_counts = pd.DataFrame(columns=['이름', '주간현황'])

        # 데이터 병합
        if not df_static.empty:
            # 1) 주간 목표 분리 (지표타입 == '주간목표')
            weekly_goals_df = df_static[df_static['지표타입'] == '주간목표']
            # 중복 제거 후 이름별 목표값 매핑
            weekly_goal_map = weekly_goals_df.set_index('이름')['목표값'].to_dict()
            
            # 2) 나머지 신체 지표만 남기기
            df_full = df_static[df_static['지표타입'] != '주간목표'].copy()
            
            if not df_meas.empty:
                df_full = pd.merge(df_full, df_meas[['이름', '지표타입', '현재값']], on=['이름', '지표타입'], how='left')
                df_full['현재값'] = df_full['현재값'].fillna(df_full['초기값'])
            else:
                df_full['현재값'] = df_full['초기값']
                
            df_full = pd.merge(df_full, weekly_counts, on='이름', how='left')
            df_full['주간현황'] = df_full['주간현황'].fillna(0)
            
            # 주간 목표 매핑 (없으면 기본값 3)
            df_full['주간목표'] = df_full['이름'].map(weekly_goal_map).fillna(3).astype(int)
            
        else:
            df_full = pd.DataFrame()

        return df_full, df_history
        
    except Exception as e:
        st.error(f"데이터 로드 오류: {e}")
        return pd.DataFrame(), pd.DataFrame()

def calculate_achievement(row):
    try:
        initial = float(row['초기값'])
        goal = float(row['목표값'])
        current = float(row['현재값'])
        metric_type = row['지표타입']
        
        if metric_type == '체중': 
            if initial == goal: return 0.0
            rate = ((initial - current) / (initial - goal)) * 100
        else: 
            if goal == initial: return 0.0
            rate = ((current - initial) / (goal - initial)) * 100
        return max(rate, 0.0)
    except:
        return 0.0

def update_measurement(name, metric_type, new_val):
    try:
        df_meas = conn.read(worksheet="measurements", ttl=0)
        mask = (df_meas['이름'] == name) & (df_meas['지표타입'] == metric_type)
        
        if df_meas[mask].empty:
            new_row = pd.DataFrame([{'이름': name, '지표타입': metric_type, '현재값': new_val, '최근인증': datetime.now().strftime("%Y-%m-%d")}])
            df_meas = pd.concat([df_meas, new_row], ignore_index=True)
        else:
            idx = df_meas[mask].index[0]
            df_meas.at[idx, '현재값'] = new_val
            df_meas.at[idx, '최근인증'] = datetime.now().strftime("%Y-%m-%d")
            
        conn.update(worksheet="measurements", data=df_meas)
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(f"저장 실패: {e}")
        return False


def log_workout(name, weekly_goal, filename):
    try:
        df_history = conn.read(worksheet="workout_history", ttl=0)
        new_row = pd.DataFrame([{
            '이름': name,
            '주간목표': weekly_goal,
            '날짜': datetime.now().strftime("%Y-%m-%d"),
            '달성여부': 'Y',
            '이미지URL': filename
        }])
        updated_df = pd.concat([df_history, new_row], ignore_index=True)
        conn.update(worksheet="workout_history", data=updated_df)
        st.cache_data.clear()
        return True
    except Exception as e:
        # Create sheet if missing
        try:
             new_row = pd.DataFrame([{
                '이름': name,
                '주간목표': weekly_goal,
                '날짜': datetime.now().strftime("%Y-%m-%d"),
                '달성여부': 'Y',
                '이미지URL': filename
            }])
             conn.update(worksheet="workout_history", data=new_row)
             st.cache_data.clear()
             return True
        except:
             st.error(f"실패: {e}")
             return False

# -----------------------------------------------------------------------------
# 3. 메인 로직
# -----------------------------------------------------------------------------
df_full, df_history = load_data()

if df_full.empty:
    st.warning("데이터 연결 대기 중...")
    st.stop()

df_full['달성률'] = df_full.apply(calculate_achievement, axis=1)

# --- 랭킹 집계 ---
# 1. 스펙왕 (달성률)
rank_spec = df_full.groupby('이름')['달성률'].mean().reset_index().sort_values('달성률', ascending=False)

# 2. 출석왕 (주간현황)
# 주간현황, 주간목표는 이름별로 Max값 가져오면 됨
rank_workout = df_full.groupby('이름').agg({'주간현황': 'max', '주간목표': 'max'}).reset_index()
# 달성비율 (Ratio)
rank_workout['진행률'] = rank_workout.apply(lambda x: min(x['주간현황']/x['주간목표'], 1.0) if x['주간목표'] > 0 else 0, axis=1)
rank_workout = rank_workout.sort_values(['진행률', '주간현황'], ascending=[False, False])

# -----------------------------------------------------------------------------
# 4. UI
# -----------------------------------------------------------------------------
st.title("💪 득근둑근")

# [1] Dual Leaderboards
c1, c2 = st.columns(2)
with c1:
    st.markdown("### 🏆 득근/다이어트 랭킹")
    st.caption("목표 달성률 기준")
    for i, (idx, row) in enumerate(rank_spec.head(3).iterrows()):
        st.markdown(f"""<div class="hof-card" style="background-color:#e3f2fd;">
            <div class="hof-rank">{["🥇","🥈","🥉"][i]}</div>
            <div class="hof-name">{row['이름']}</div>
            <div class="hof-score">{row['달성률']:.1f}%</div>
        </div>""", unsafe_allow_html=True)

with c2:
    st.markdown("### 🔥 성실함 랭킹 (출석왕)")
    st.caption("주간 운동 목표 달성 기준")
    for i, (idx, row) in enumerate(rank_workout.head(3).iterrows()):
        st.markdown(f"""<div class="hof-card" style="background-color:#fff3e0;">
            <div class="hof-rank">{["🥇","🥈","🥉"][i]}</div>
            <div class="hof-name">{row['이름']}</div>
            <div class="hof-score">{int(row['주간현황'])} / {int(row['주간목표'])}</div>
        </div>""", unsafe_allow_html=True)

st.divider()

# [2] My Page (Tabs separated)
st.markdown("### 📝 기록하기")
user_list = ["선택해주세요"] + sorted(rank_spec['이름'].unique().tolist())
selected_user = st.selectbox("본인 확인", user_list)

if selected_user != "선택해주세요":
    u_spec = rank_spec[rank_spec['이름'] == selected_user].iloc[0]
    u_work = rank_workout[rank_workout['이름'] == selected_user].iloc[0]
    
    # 탭 분리
    tab_body, tab_work = st.tabs(["💪 신체 변화 기록", "📸 오운완 인증"])
    
    with tab_body:
        st.info(f"현재 평균 목표 달성률: **{u_spec['달성률']:.1f}%**")
        with st.form("body_form"):
            my_metrics = df_full[df_full['이름'] == selected_user]
            inputs = {}
            for _, r in my_metrics.iterrows():
                mt = r['지표타입']
                inputs[mt] = st.number_input(f"{mt} 현재값 (kg/%)", min_value=0.0, step=0.1, key=f"k_{mt}")
            
            if st.form_submit_button("신체 수치 저장"):
                with st.spinner("저장 중..."):
                    updated = False
                    for mt, val in inputs.items():
                        if val > 0:
                            update_measurement(selected_user, mt, val)
                            updated = True
                    if updated:
                        st.toast("신체 정보 업데이트 완료!", icon="✅")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.warning("입력된 값이 없습니다.")

    with tab_work:
        st.info(f"이번 주 운동: **{int(u_work['주간현황'])} / {int(u_work['주간목표'])}** 회")
        with st.form("workout_form"):
            uploaded = st.file_uploader("인증 사진 업로드", type=['jpg', 'png'])
            # 이미지 미리보기
            if uploaded:
                st.image(uploaded, caption="업로드 예정 사진", width=300)

            if st.form_submit_button("오운완 저장"):
                if uploaded:
                    with st.spinner("Cloudinary 업로드 및 기록 중..."):
                        # 파일명 생성
                        fname = f"{selected_user}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                        
                        # [Cloudinary 업로드]
                        image_url = upload_to_cloudinary(uploaded.getvalue())
                        
                        if image_url:
                            # 시트에는 이미지 URL 저장
                            log_workout(selected_user, int(u_work['주간목표']), image_url)
                            
                            st.toast("오늘도 고생하셨습니다! (+1회)", icon="🔥")
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error("업로드에 실패했습니다. Cloudinary 설정을 확인하세요.")
                else:
                    st.warning("사진을 업로드해주세요.")


st.divider()

# [3] Dashboard
st.markdown("### 📊 공동체 현황")
t1, t2 = st.tabs(["주간 현황", "최근 인증 로그"])

with t1:
    for i, row in rank_workout.iterrows():
        c1, c2, c3 = st.columns([1.5, 3, 1])
        with c1: st.text(row['이름'])
        with c2:
            ratio = min(row['주간현황']/row['주간목표'], 1.0) if row['주간목표'] > 0 else 0
            st.progress(ratio)
        with c3: st.caption(f"{int(row['주간현황'])}/{int(row['주간목표'])}")

with t2:
    if not df_history.empty and '날짜' in df_history.columns:
        # 최근 50개만 표시
        df_show = df_history.sort_values('날짜', ascending=False).head(50)
        
        st.dataframe(
            df_show, 
            column_config={
                "이미지URL": st.column_config.LinkColumn(
                    "인증샷", 
                    display_text="📸 보기",
                    help="클릭하면 사진을 확인합니다"
                )
            },
            hide_index=True,
            use_container_width=True
        )
    elif not df_history.empty:
        st.warning("'날짜' 컬럼을 찾을 수 없습니다. 시트 헤더를 확인해주세요.")
        st.dataframe(df_history, use_container_width=True)
    else:
        st.text("아직 인증 내역이 없습니다.")
