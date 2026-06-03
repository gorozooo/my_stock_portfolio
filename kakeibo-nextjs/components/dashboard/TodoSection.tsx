'use client'
import { useState, useTransition } from 'react'
import { CheckCircle2, Circle, Trash2, Plus } from 'lucide-react'
import { addTodoAction, toggleTodoAction, deleteTodoAction } from '@/actions/todo'
import { toast } from 'sonner'
import type { MonthlyTodo } from '@/lib/kakeibo/types'

interface Props {
  todos: MonthlyTodo[]
  month: string
}

export default function TodoSection({ todos: initialTodos, month }: Props) {
  const [todos, setTodos] = useState(initialTodos)
  const [newTitle, setNewTitle] = useState('')
  const [showInput, setShowInput] = useState(false)
  const [isPending, startTransition] = useTransition()

  function handleToggle(todo: MonthlyTodo) {
    const newStatus = todo.status === 'DONE' ? 'TODO' : 'DONE'
    setTodos(prev => prev.map(t => t.id === todo.id ? { ...t, status: newStatus } : t))
    startTransition(async () => {
      const res = await toggleTodoAction(todo.id, todo.status)
      if (res?.error) toast.error(res.error)
    })
  }

  function handleDelete(id: number) {
    setTodos(prev => prev.filter(t => t.id !== id))
    startTransition(async () => {
      const res = await deleteTodoAction(id)
      if (res?.error) toast.error(res.error)
    })
  }

  function handleAdd() {
    if (!newTitle.trim()) return
    const title = newTitle.trim()
    setNewTitle('')
    setShowInput(false)
    startTransition(async () => {
      const res = await addTodoAction(month, title)
      if (res?.error) toast.error(res.error)
    })
  }

  const done = todos.filter(t => t.status === 'DONE').length

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <span className="section-title">今月のTODO</span>
        <span className="text-xs text-[var(--kb-muted)]">{done}/{todos.length}</span>
      </div>
      <div className="card divide-y">
        {todos.length === 0 && !showInput && (
          <p className="text-sm text-[var(--kb-muted)] text-center py-4">TODOはありません</p>
        )}
        {todos.map(todo => (
          <div key={todo.id} className="flex items-center gap-3 px-4 py-3">
            <button onClick={() => handleToggle(todo)} className="flex-shrink-0 transition-transform active:scale-90">
              {todo.status === 'DONE'
                ? <CheckCircle2 size={20} className="text-[var(--kb-income)]" />
                : <Circle size={20} className="text-[var(--kb-muted)]" />
              }
            </button>
            <span className={`flex-1 text-sm ${todo.status === 'DONE' ? 'line-through text-[var(--kb-muted)]' : 'text-[var(--kb-text)]'}`}>
              {todo.title}
            </span>
            <button onClick={() => handleDelete(todo.id)} className="text-[var(--kb-muted)] hover:text-red-500 transition-colors p-1">
              <Trash2 size={14} />
            </button>
          </div>
        ))}
        {showInput && (
          <div className="flex items-center gap-2 px-4 py-3">
            <input
              type="text"
              value={newTitle}
              onChange={e => setNewTitle(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleAdd()}
              autoFocus
              className="flex-1 text-sm border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
              placeholder="新しいTODO..."
            />
            <button onClick={handleAdd} className="bg-blue-600 text-white text-sm px-3 py-2 rounded-lg font-medium">追加</button>
            <button onClick={() => setShowInput(false)} className="text-[var(--kb-muted)] text-sm px-2 py-2">キャンセル</button>
          </div>
        )}
        {!showInput && (
          <button
            onClick={() => setShowInput(true)}
            className="flex items-center gap-2 px-4 py-3 text-sm text-blue-600 hover:bg-blue-50 transition-colors w-full"
          >
            <Plus size={16} />
            TODOを追加
          </button>
        )}
      </div>
    </div>
  )
}
