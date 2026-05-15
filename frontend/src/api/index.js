import axios from 'axios'

const http = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

export const candidateApi = {
  list: (params = {}) => http.get('/candidates', { params }),
  getHistory: (id) => http.get(`/candidates/${id}/history`),
}

export const resumeApi = {
  upload: (formData, candidateId) => {
    const params = candidateId ? { candidate_id: candidateId } : {}
    return http.post('/resume/upload', formData, { params })
  },
  getProfile: (candidateId) =>
    http.get('/resume/profile', { params: { candidate_id: candidateId } }),
}

export const questionApi = {
  get: (candidateId) =>
    http.get('/interview/questions', { params: { candidate_id: candidateId } }),
  update: (data) => http.put('/interview/questions', data),
}

export const interviewApi = {
  start: (candidateId, triggerMode = 'auto') =>
    http.post('/interview/start', { candidate_id: candidateId, trigger_mode: triggerMode }),
  stop: () => http.post('/interview/stop'),
  suggest: () => http.post('/interview/suggest'),
  getEval: (interviewId) =>
    http.get('/interview/eval', interviewId ? { params: { interview_id: interviewId } } : {}),
}

export const sessionApi = {
  current: () => http.get('/session/current'),
  switch: (targetAgent) => http.post('/session/switch', { target_agent: targetAgent }),
}