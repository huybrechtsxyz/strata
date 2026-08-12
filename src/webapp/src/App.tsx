import StatusPanel from './components/StatusPanel'
import WorkspacesPanel from './components/WorkspacesPanel'
import TailPanel from './components/TailPanel'
import './App.css'

function App() {
  return (
    <div className="dashboard">
      <h1>strata state-service</h1>
      <div className="dashboard-row">
        <StatusPanel />
        <WorkspacesPanel />
      </div>
      <TailPanel />
    </div>
  )
}

export default App
