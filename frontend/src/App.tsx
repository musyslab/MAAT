// App.tsx: Defines the public, student, and administrator routes for the application.
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import axios from 'axios';

import LoginPage from './pages/public/Login';
import HomePage from './pages/public/HomePage';
import NotFound from './pages/public/NotFound';
import SchoolLoginSelect from './pages/public/SchoolLoginSelect';
import SchoolSelect from './pages/public/SchoolSelect';

import StudentUpload from './pages/student/StudentUpload';
import StudentClassSelection from './pages/student/StudentClassSelection';
import StudentModuleList from './pages/student/StudentModuleList';
import StudentModuleDetails from './pages/student/StudentModuleDetails';

import AdminClassSelect from './pages/admin/AdminClassSelect';
import AdminMenu from './pages/admin/AdminMenu';
import AdminAnalyticsDashboard from './pages/admin/AdminAnalyticsDashboard';
import AdminGrading from './pages/admin/AdminGrading';
import AdminModuleList from './pages/admin/AdminModuleList';
import AdminModuleDetails from './pages/admin/AdminModuleDetails';
import AdminOfficeHours from './pages/admin/AdminOfficeHours';
import AdminProjectManage from './pages/admin/AdminProjectManage';
import AdminStudentList from './pages/admin/AdminStudentList';
import AdminUpload from './pages/admin/AdminUpload';
import AdminViewStudentCode from './pages/admin/AdminViewStudentCode';

import ProtectedRoute from './pages/components/ProtectedRoute';

// Redirects to school login when the server rejects an expired or invalid session.
axios.interceptors.response.use(
    undefined,
    (error) => {
        if ([401, 422, 419].includes(error.response?.status)) {
            localStorage.removeItem("AUTOTA_AUTH_TOKEN");
            window.location.href = "/school-login";
        }
        return Promise.reject(error);
    },
);

// Defines the public, student, and administrator routes for the application.
function App() {
    // Renders the interface using the current data and interaction state.
    return (
        <BrowserRouter>
            {/* Matches the current URL to its page component. */}
            <Routes>
                <Route path="/school-login" element={<SchoolLoginSelect />} />
                <Route path="/login" element={<LoginPage />} />

                <Route path="/" element={<HomePage />} />

                <Route element={<ProtectedRoute />}>
                    <Route path="/schools" element={<SchoolSelect />} />

                    <Route path="/admin/school/:school_id/classes" element={<AdminClassSelect />} />
                    <Route path="/admin/school/:school_id/class/:class_id/menu" element={<AdminMenu />} />
                    <Route path="/admin/school/:school_id/class/:class_id/student-preview" element={<StudentModuleList />} />
                    <Route path="/admin/school/:school_id/class/:class_id/student-preview/module/:module_id" element={<StudentModuleDetails />} />
                    <Route path="/admin/school/:school_id/class/:class_id/student-preview/module/:module_id/project/:project_id/upload" element={<StudentUpload />} />
                    <Route path="/admin/school/:school_id/class/:class_id/student-preview/module/:module_id/project/:project_id/checkpoint/:checkpoint_id/upload" element={<StudentUpload />} />
                    <Route path="/admin/school/:school_id/class/:class_id/office-hours" element={<AdminOfficeHours />} />
                    <Route path="/admin/school/:school_id/class/:class_id/upload" element={<AdminUpload />} />
                    <Route path="/admin/school/:school_id/class/:class_id/analytics" element={<AdminAnalyticsDashboard />} />
                    <Route path="/admin/school/:school_id/class/:class_id/modules/*" element={<AdminModuleList />} />

                    <Route path="/admin/school/:school_id/class/:class_id/module/:module_id/overview" element={<AdminModuleDetails />} />

                    <Route path="/admin/school/:school_id/class/:class_id/module/:module_id/project/:id/submissions" element={<AdminStudentList />} />
                    <Route path="/admin/school/:school_id/class/:class_id/module/:module_id/project/:id/manage" element={<AdminProjectManage />} />

                    <Route path="/admin/school/:school_id/class/:class_id/module/:module_id/project/:id/checkpoint/:checkpoint_id/submissions" element={<AdminStudentList />} />
                    <Route path="/admin/school/:school_id/class/:class_id/module/:module_id/project/:id/checkpoint/:checkpoint_id/manage" element={<AdminProjectManage practiceMode />} />

                    <Route path="/admin/school/:school_id/class/:class_id/module/:module_id/project/:project_id/grade/:id" element={<AdminGrading />} />
                    <Route path="/admin/school/:school_id/class/:class_id/module/:module_id/project/:project_id/checkpoint/:checkpoint_id/grade/:id" element={<AdminGrading />} />
                    <Route path="/admin/school/:school_id/class/:class_id/module/:module_id/project/:project_id/codeview/:id" element={<AdminViewStudentCode />} />
                    <Route path="/admin/school/:school_id/class/:class_id/module/:module_id/project/:project_id/checkpoint/:checkpoint_id/codeview/:id" element={<AdminViewStudentCode />} />


                    <Route path="/student/school/:school_id/classes" element={<StudentClassSelection />} />
                    <Route path="/student/school/:school_id/class/:class_id/modules/*" element={<StudentModuleList />} />
                    <Route path="/student/school/:school_id/class/:class_id/module/:module_id" element={<StudentModuleDetails />} />
                    <Route path="/student/school/:school_id/class/:class_id/module/:module_id/project/:project_id/upload" element={<StudentUpload />} />
                    <Route path="/student/school/:school_id/class/:class_id/module/:module_id/project/:project_id/checkpoint/:checkpoint_id/upload" element={<StudentUpload />} />

                </Route>

                <Route path="*" element={<NotFound />} />
            </Routes>
        </BrowserRouter>
    );
}

export default App;
