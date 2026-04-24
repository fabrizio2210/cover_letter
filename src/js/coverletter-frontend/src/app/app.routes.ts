import { Routes } from '@angular/router';
import { LoginComponent } from './features/auth/login.component';
import { DashboardComponent } from './features/dashboard/dashboard.component';
import { DashboardOverviewComponent } from './features/dashboard/dashboard-overview.component';
import { JobDiscoveryComponent } from './features/job-discovery/job-discovery.component';
import { IdentitiesComponent } from './features/identities/identities.component';
import { CoverLettersListComponent } from './features/cover-letters/coverletters-list.component';
import { RecipientsComponent } from './features/recipients/recipients.component';
import { SettingsComponent } from './features/settings/settings.component';
import { LetterEditorComponent } from './features/cover-letters/letter-editor.component';
import { authGuard } from './core/auth/auth.guard';

export const routes: Routes = [
    { path: 'login', component: LoginComponent },
    { path: 'settings', redirectTo: '/dashboard/settings', pathMatch: 'full' },

    // Dashboard acts as a shell with sidebar nav and router-outlet for child pages
    {
        path: 'dashboard',
        component: DashboardComponent,
        canActivate: [authGuard],
        children: [
            { path: '', component: DashboardOverviewComponent }, // Overview page with stats & opportunities
            { path: 'job-discovery', component: JobDiscoveryComponent },
            { path: 'identities', component: IdentitiesComponent },
            { path: 'settings', component: SettingsComponent },
            { path: 'fields', redirectTo: 'settings', pathMatch: 'full' },
            { path: 'letter-editor/:id', component: LetterEditorComponent },
            { path: 'cover-letters', component: CoverLettersListComponent },
            { path: 'cover-letters/:id', redirectTo: 'letter-editor/:id', pathMatch: 'full' },
            { path: 'recipients', component: RecipientsComponent },
            { path: 'companies', redirectTo: 'recipients', pathMatch: 'full' }
        ]
    },

    // Keep top-level redirect to the login page by default
    { path: '', redirectTo: '/login', pathMatch: 'full' }
];
