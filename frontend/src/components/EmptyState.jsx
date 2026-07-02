// État vide homogène pour les listes (vivier, clients, AO, PAC, partenaires).
// Un seul composant → même icône (32px), même espacement, même couleur de
// texte partout, au lieu des variantes « card p-10 » / « py-16 » et
// slate-400 / slate-500 qui coexistaient page par page.
//
//   <EmptyState icon={Users} message="Aucun résultat" action={<Link .../>} />
export function EmptyState({ icon: Icon, message, action = null }) {
  return (
    <div className="text-center py-16">
      {Icon && <Icon size={32} className="mx-auto text-slate-700 mb-3" />}
      <p className="text-slate-400 text-sm">{message}</p>
      {action}
    </div>
  )
}
