<template>
  <div class="console">
    <div class="top-bar">
      <div class="session-info">
        <span class="stage-badge" :class="store.stage">{{ stageLabel }}</span>
        <span v-if="store.candidateName" class="candidate-name">{{ store.candidateName }}</span>
        <span class="rounds">{{ store.roundsCount }} 轮对话</span>
      </div>
      <div class="top-controls">
        <div class="token-bar">
          <span class="token-text">{{ store.tokenUsed.toLocaleString() }} / {{ store.tokenBudget.toLocaleString() }} tokens</span>
          <div class="progress-track">
            <div class="progress-fill" :style="{ width: tokenPct + '%' }"></div>
          </div>
        </div>
        <button class="btn btn-stop" @click="stopInterview" :disabled="stopping">
          {{ stopping ? '结束中...' : '结束面试' }}
        </button>
      </div>
    </div>

    <div class="main-area">
      <div class="transcript-panel">
        <div class="panel-title">转写记录</div>
        <div class="transcript-scroll" ref="transcriptRef">
          <div v-if="store.transcript.length === 0" class="empty-msg">等待音频输入...</div>
          <div
            v-for="(seg, i) in store.transcript"
            :key="i"
            class="segment"
            :class="seg.source"
          >
            <div class="seg-header">
              <span class="speaker-tag">{{ seg.source === 'candidate' ? '候选人' : '面试官' }}</span>
              <span class="seg-time">{{ seg.timestamp }}</span>
            </div>
            <div class="seg-text">{{ seg.text }}</div>
          </div>
        </div>
        <div class="manual-input">
          <select v-model="manualSource" class="source-select">
            <option value="candidate">候选人</option>
            <option value="interviewer">面试官</option>
          </select>
          <input
            v-model="manualText"
            class="text-input"
            placeholder="手动输入文字 (Enter 发送)..."
            @keydown.enter="sendManualInput"
          />
          <button class="btn btn-sm" @click="sendManualInput">发送</button>
        </div>
      </div>

      <div class="suggestion-panel">
        <div class="panel-title">追问建议</div>
        <div class="suggestion-content">
          <div v-if="!store.currentSuggestion && store.suggestionHistory.length === 0" class="empty-msg">
            等待建议生成...
          </div>
          <div v-if="store.currentSuggestion" class="suggestion-current">
            {{ store.currentSuggestion }}<span v-if="isStreaming" class="cursor">|</span>
          </div>
        </div>

        <div v-if="store.suggestionHistory.length > 0" class="suggestion-history">
          <div class="history-title">历史建议</div>
          <div
            v-for="(s, i) in [...store.suggestionHistory].reverse().slice(0, 3)"
            :key="i"
            class="history-item"
          >{{ s }}</div>
        </div>

        <div class="suggestion-controls">
          <div class="trigger-mode">
            <span class="mode-label">触发模式</span>
            <button
              class="mode-btn"
              :class="{ active: store.triggerMode === 'auto' }"
              @click="setTriggerMode('auto')"
            >自动</button>
            <button
              class="mode-btn"
              :class="{ active: store.triggerMode === 'manual' }"
              @click="setTriggerMode('manual')"
            >手动</button>
          </div>
          <button class="btn btn-trigger" @click="triggerSuggest" :disabled="isStreaming">
            立即生成建议
          </button>
        </div>
      </div>
    </div>

    <div v-if="stopError" class="error-banner">{{ stopError }}</div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useInterviewStore } from '../stores/interview.js'
import { interviewApi } from '../api/index.js'

const router = useRouter()
const store = useInterviewStore()

const stopping = ref(false)
const stopError = ref('')
const manualSource = ref('candidate')
const manualText = ref('')
const transcriptRef = ref(null)
const isStreaming = ref(false)
let streamingTimer = null

const stageLabel = computed(() => {
  const labels = { idle: '空闲', resume_analysis: '简历分析', interviewing: '面试中', evaluating: '评价中' }
  return labels[store.stage] ?? store.stage
})

const tokenPct = computed(() =>
  Math.min(100, Math.round((store.tokenUsed / store.tokenBudget) * 100))
)

watch(() => store.currentSuggestion, (v) => {
  if (v) {
    isStreaming.value = true
    clearTimeout(streamingTimer)
    streamingTimer = setTimeout(() => { isStreaming.value = false }, 1500)
  }
})

watch(() => store.transcript.length, () => {
  nextTick(() => {
    if (transcriptRef.value) {
      transcriptRef.value.scrollTop = transcriptRef.value.scrollHeight
    }
  })
})

function triggerSuggest() {
  store.send({ type: 'request_suggestion' })
}

function setTriggerMode(mode) {
  store.send({ type: 'set_trigger_mode', mode })
}

function sendManualInput() {
  const text = manualText.value.trim()
  if (!text) return
  store.send({ type: 'manual_input', source: manualSource.value, text })
  manualText.value = ''
}

async function stopInterview() {
  stopping.value = true
  stopError.value = ''
  try {
    await interviewApi.stop()
    await router.push('/report')
  } catch (e) {
    stopError.value = e.response?.data?.detail?.message ?? '结束失败，请重试'
  } finally {
    stopping.value = false
  }
}

onUnmounted(() => clearTimeout(streamingTimer))
</script>

<style scoped>
.console {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 52px);
  overflow: hidden;
}

.top-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1.5rem;
  background: #1f2937;
  border-bottom: 1px solid #374151;
  flex-shrink: 0;
}

.session-info { display: flex; align-items: center; gap: 1rem; font-size: 0.9rem; }

.stage-badge {
  padding: 0.25rem 0.6rem;
  border-radius: 4px;
  font-size: 0.75rem;
  font-weight: 600;
}
.stage-badge.interviewing { background: #064e3b; color: #6ee7b7; }
.stage-badge.evaluating { background: #312e81; color: #a5b4fc; }
.stage-badge.idle, .stage-badge.resume_analysis { background: #374151; color: #9ca3af; }

.candidate-name { color: #e5e7eb; }
.rounds { color: #6b7280; font-size: 0.8rem; }

.top-controls { display: flex; align-items: center; gap: 1.5rem; }

.token-bar { font-size: 0.75rem; color: #9ca3af; }
.progress-track { height: 4px; width: 120px; background: #374151; border-radius: 2px; margin-top: 4px; }
.progress-fill { height: 100%; background: #3b82f6; border-radius: 2px; transition: width 0.3s; }

.main-area { display: flex; flex: 1; overflow: hidden; }

.transcript-panel {
  flex: 3;
  display: flex;
  flex-direction: column;
  border-right: 1px solid #374151;
}
.suggestion-panel {
  flex: 2;
  display: flex;
  flex-direction: column;
}

.panel-title {
  padding: 0.6rem 1rem;
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #6b7280;
  border-bottom: 1px solid #374151;
  flex-shrink: 0;
}

.transcript-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.segment { padding: 0.75rem; border-radius: 8px; font-size: 0.9rem; }
.segment.candidate { background: #1e3a5f; }
.segment.interviewer { background: #1f2937; border: 1px solid #374151; }

.seg-header { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.4rem; }
.speaker-tag { font-size: 0.72rem; font-weight: 600; color: #9ca3af; }
.seg-time { font-size: 0.7rem; color: #4b5563; margin-left: auto; }
.seg-text { line-height: 1.55; }

.manual-input {
  display: flex;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
  border-top: 1px solid #374151;
  flex-shrink: 0;
}

.source-select {
  background: #374151;
  border: none;
  border-radius: 4px;
  color: #d1d5db;
  padding: 0.4rem 0.5rem;
  font-size: 0.8rem;
  cursor: pointer;
  outline: none;
}

.text-input {
  flex: 1;
  background: #1f2937;
  border: 1px solid #374151;
  border-radius: 4px;
  color: #f9fafb;
  padding: 0.4rem 0.6rem;
  font-size: 0.85rem;
  outline: none;
}
.text-input:focus { border-color: #3b82f6; }

.suggestion-content {
  flex: 1;
  padding: 1rem;
  overflow-y: auto;
}
.suggestion-current {
  font-size: 1rem;
  line-height: 1.75;
  color: #e5e7eb;
  white-space: pre-wrap;
}
.cursor {
  display: inline-block;
  animation: blink 0.7s infinite;
  color: #3b82f6;
  font-weight: 300;
}
@keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }

.suggestion-history {
  padding: 0 1rem 0.75rem;
  border-top: 1px solid #374151;
}
.history-title {
  font-size: 0.68rem;
  text-transform: uppercase;
  color: #4b5563;
  padding: 0.5rem 0;
}
.history-item {
  font-size: 0.8rem;
  color: #6b7280;
  padding: 0.4rem 0;
  border-bottom: 1px solid #1f2937;
  line-height: 1.5;
}

.suggestion-controls {
  padding: 1rem;
  border-top: 1px solid #374151;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.trigger-mode { display: flex; align-items: center; gap: 0.5rem; }
.mode-label { font-size: 0.8rem; color: #9ca3af; }
.mode-btn {
  padding: 0.3rem 0.75rem;
  border-radius: 4px;
  background: #374151;
  border: 1px solid #4b5563;
  color: #9ca3af;
  cursor: pointer;
  font-size: 0.8rem;
  transition: all 0.15s;
}
.mode-btn.active { background: #1e3a5f; border-color: #3b82f6; color: #93c5fd; }

.empty-msg { color: #4b5563; font-size: 0.85rem; }

.btn {
  padding: 0.5rem 1rem;
  border-radius: 6px;
  font-size: 0.85rem;
  cursor: pointer;
  border: none;
  transition: opacity 0.15s;
}
.btn:hover:not(:disabled) { opacity: 0.85; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-stop { background: #7f1d1d; color: #fca5a5; }
.btn-trigger { background: #3b82f6; color: #fff; }
.btn-sm { background: #374151; color: #d1d5db; padding: 0.35rem 0.75rem; }

.error-banner {
  background: #7f1d1d;
  color: #fca5a5;
  padding: 0.5rem 1rem;
  font-size: 0.85rem;
  text-align: center;
  flex-shrink: 0;
}
</style>