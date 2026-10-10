interface TeamLogoProps {
  name: string
  size?: 'sm' | 'md' | 'lg' | 'xl'
  className?: string
  sport?: 'NBA' | 'NFL' | string
}

const TEAM_PALETTES: Record<string, { bg: string; text: string; abbr: string }> = {
  // NBA
  'Boston Celtics': { bg: 'from-emerald-600 to-green-800', text: 'text-white', abbr: 'BOS' },
  'Los Angeles Lakers': { bg: 'from-amber-500 to-purple-800', text: 'text-amber-100', abbr: 'LAL' },
  'Golden State Warriors': { bg: 'from-blue-600 to-amber-500', text: 'text-white', abbr: 'GSW' },
  'Denver Nuggets': { bg: 'from-blue-800 to-amber-600', text: 'text-white', abbr: 'DEN' },
  'Milwaukee Bucks': { bg: 'from-emerald-800 to-stone-400', text: 'text-white', abbr: 'MIL' },
  'Miami Heat': { bg: 'from-rose-700 to-amber-600', text: 'text-white', abbr: 'MIA' },
  'Phoenix Suns': { bg: 'from-purple-800 to-orange-600', text: 'text-orange-200', abbr: 'PHX' },
  'Dallas Mavericks': { bg: 'from-blue-700 to-cyan-500', text: 'text-white', abbr: 'DAL' },
  'Philadelphia 76ers': { bg: 'from-blue-700 to-rose-600', text: 'text-white', abbr: 'PHI' },
  'New York Knicks': { bg: 'from-blue-600 to-orange-500', text: 'text-white', abbr: 'NYK' },
  'Oklahoma City Thunder': { bg: 'from-sky-500 to-orange-600', text: 'text-white', abbr: 'OKC' },
  'Minnesota Timberwolves': { bg: 'from-blue-900 to-lime-500', text: 'text-white', abbr: 'MIN' },
  'Cleveland Cavaliers': { bg: 'from-red-900 to-amber-500', text: 'text-amber-200', abbr: 'CLE' },
  'LA Clippers': { bg: 'from-blue-800 to-red-600', text: 'text-white', abbr: 'LAC' },
  'Memphis Grizzlies': { bg: 'from-sky-800 to-blue-950', text: 'text-sky-200', abbr: 'MEM' },
  'Sacramento Kings': { bg: 'from-purple-900 to-stone-500', text: 'text-purple-200', abbr: 'SAC' },
  // NFL
  'Kansas City Chiefs': { bg: 'from-red-600 to-amber-500', text: 'text-white', abbr: 'KC' },
  'San Francisco 49ers': { bg: 'from-red-700 to-amber-600', text: 'text-white', abbr: 'SF' },
  'Philadelphia Eagles': { bg: 'from-emerald-900 to-stone-400', text: 'text-emerald-100', abbr: 'PHI' },
  'Baltimore Ravens': { bg: 'from-purple-900 to-black', text: 'text-purple-200', abbr: 'BAL' },
  'Buffalo Bills': { bg: 'from-blue-700 to-red-600', text: 'text-white', abbr: 'BUF' },
  'Dallas Cowboys': { bg: 'from-blue-950 to-stone-400', text: 'text-blue-100', abbr: 'DAL' },
  'Detroit Lions': { bg: 'from-sky-600 to-stone-400', text: 'text-white', abbr: 'DET' },
  'Miami Dolphins': { bg: 'from-teal-600 to-orange-500', text: 'text-white', abbr: 'MIA' },
}

export function TeamLogo({ name, size = 'md', className = '' }: TeamLogoProps) {
  const palette = TEAM_PALETTES[name] ?? {
    bg: 'from-emerald-700/60 to-teal-900/60',
    text: 'text-emerald-100',
    abbr: name.split(' ').map((n) => n[0]).join('').slice(0, 3).toUpperCase(),
  }

  const sizeClasses = {
    sm: 'h-6 w-6 text-[10px]',
    md: 'h-9 w-9 text-xs',
    lg: 'h-12 w-12 text-sm',
    xl: 'h-16 w-16 text-lg font-bold sm:h-20 sm:w-20 sm:text-xl',
  }

  return (
    <div
      title={name}
      className={`inline-grid shrink-0 place-items-center rounded-xl bg-gradient-to-br border border-white/15 font-mono font-bold tracking-wider shadow-md select-none ${palette.bg} ${palette.text} ${sizeClasses[size]} ${className}`}
    >
      <span>{palette.abbr}</span>
    </div>
  )
}
