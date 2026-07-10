import { Component } from 'react'

/*
 * Filet de sécurité de rendu. Sans lui, une erreur pendant le rendu (typiquement
 * un « chunk » lazy introuvable après un nouveau déploiement) démonte tout l'arbre
 * React → page blanche. Ici on affiche un message clair + un bouton « Recharger »
 * au lieu d'un écran vide qui déstabilise l'utilisateur.
 *
 * Le cas courant (chunk périmé) est déjà récupéré automatiquement en amont par
 * lazyWithReload (App.jsx) ; ce composant couvre le reste (échec réel, erreur de
 * rendu inattendue).
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError() {
    return { hasError: true }
  }

  componentDidCatch(error, info) {
    // Un rendu qui casse ne doit pas rester silencieux (diagnostic console).
    console.error('[ErrorBoundary]', error, info)
  }

  handleReload = () => {
    try { sessionStorage.removeItem('chunk-reload-once') } catch { /* ignore */ }
    window.location.reload()
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-[60vh] flex flex-col items-center justify-center gap-4 p-10 text-center">
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
            Une erreur est survenue lors du chargement de cette page.
          </p>
          <button className="btn-primary" onClick={this.handleReload}>
            Recharger la page
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
