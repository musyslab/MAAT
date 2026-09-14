import React, { Component } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
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
import AdminPlagiarism from "./pages/admin/AdminPlagiarism";
import AdminModuleList from './pages/admin/AdminModuleList';
import AdminModuleDetails from './pages/admin/AdminModuleDetails';
import AdminOfficeHours from './pages/admin/AdminOfficeHours';
import AdminProjectManage from './pages/admin/AdminProjectManage';
import AdminStudentList from './pages/admin/AdminStudentList';
import AdminUpload from './pages/admin/AdminUpload';
import AdminViewStudentCode from './pages/admin/AdminViewStudentCode';

import ProtectedRoute from './pages/components/ProtectedRoute';

const redirectLegacyAdminRoute = () => {
    return "/admin/schools";
};

const configureAxiosInterceptors = () => {
    axios.interceptors.response.use(
        function (successRes) {
            return successRes;
        },
        function (error) {
            if (error.response && (error.response.status === 401 || error.response.status === 422 || error.response.status === 419)) {
                localStorage.removeItem("AUTOTA_AUTH_TOKEN");
                window.location.href = "/school-login";
            }
            return Promise.reject(error);
        });
};

configureAxiosInterceptors();

class App extends Component {
    render() {
        return (
            <BrowserRouter>
                <Routes>
                    <Route path="/school-login" element={<SchoolLoginSelect />} />
                    <Route path="/login" element={<LoginPage />} />

                    <Route path="/" element={<HomePage />} />

                    <Route path="/schools" element={
                        <ProtectedRoute>
                            <SchoolSelect />
                        </ProtectedRoute>
                    } />

                    <Route path="/admin/classes" element={
                        <ProtectedRoute>
                            <Navigate to="/admin/schools" replace />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/school/:school_id/classes" element={
                        <ProtectedRoute>
                            <AdminClassSelect />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/school/:school_id/class/:class_id/menu" element={
                        <ProtectedRoute>
                            <AdminMenu />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/school/:school_id/class/:class_id/student-preview" element={
                        <ProtectedRoute>
                            <StudentModuleList />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/school/:school_id/class/:class_id/student-preview/module/:module_id" element={
                        <ProtectedRoute>
                            <StudentModuleDetails />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/school/:school_id/class/:class_id/student-preview/module/:module_id/project/:project_id/upload" element={
                        <ProtectedRoute>
                            <StudentUpload />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/school/:school_id/class/:class_id/student-preview/module/:module_id/project/:project_id/checkpoint/:checkpoint_id/upload" element={
                        <ProtectedRoute>
                            <StudentUpload />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/school/:school_id/class/:class_id/office-hours" element={
                        <ProtectedRoute>
                            <AdminOfficeHours />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/school/:school_id/class/:class_id/upload" element={
                        <ProtectedRoute>
                            <AdminUpload />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/school/:school_id/class/:class_id/analytics" element={
                        <ProtectedRoute>
                            <AdminAnalyticsDashboard />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/school/:school_id/class/:class_id/modules/*" element={
                        <ProtectedRoute>
                            <AdminModuleList />
                        </ProtectedRoute>
                    } />

                    <Route path="/admin/school/:school_id/class/:class_id/module/:module_id/overview" element={
                        <ProtectedRoute>
                            <AdminModuleDetails />
                        </ProtectedRoute>
                    } />

                    <Route path="/admin/school/:school_id/class/:class_id/module/:module_id/project/:id/overview" element={
                        <ProtectedRoute>
                            <Navigate to="../../overview" replace />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/school/:school_id/class/:class_id/module/:module_id/project/:id/submissions" element={
                        <ProtectedRoute>
                            <AdminStudentList />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/school/:school_id/class/:class_id/module/:module_id/project/:id/manage" element={
                        <ProtectedRoute>
                            <AdminProjectManage />
                        </ProtectedRoute>
                    } />

                    <Route path="/admin/school/:school_id/class/:class_id/module/:module_id/project/:id/checkpoint/:checkpoint_id/submissions" element={
                        <ProtectedRoute>
                            <AdminStudentList />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/school/:school_id/class/:class_id/module/:module_id/project/:id/checkpoint/:checkpoint_id/manage" element={
                        <ProtectedRoute>
                            <AdminProjectManage practiceMode />
                        </ProtectedRoute>
                    } />

                    <Route path="/admin/school/:school_id/class/:class_id/module/:module_id/project/:project_id/grade/:id" element={
                        <ProtectedRoute>
                            <AdminGrading />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/school/:school_id/class/:class_id/module/:module_id/project/:project_id/checkpoint/:checkpoint_id/grade/:id" element={
                        <ProtectedRoute>
                            <AdminGrading />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/school/:school_id/class/:class_id/module/:module_id/project/:project_id/codeview/:id" element={
                        <ProtectedRoute>
                            <AdminViewStudentCode />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/school/:school_id/class/:class_id/module/:module_id/project/:project_id/checkpoint/:checkpoint_id/codeview/:id" element={
                        <ProtectedRoute>
                            <AdminViewStudentCode />
                        </ProtectedRoute>
                    } />

                    <Route path="/admin/:id/modules/*" element={
                        <ProtectedRoute>
                            <Navigate to={redirectLegacyAdminRoute()} replace />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/:class_id/module/:module_id/overview" element={
                        <ProtectedRoute>
                            <Navigate to={redirectLegacyAdminRoute()} replace />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/:class_id/module/:module_id/project/:id/overview" element={
                        <ProtectedRoute>
                            <Navigate to={redirectLegacyAdminRoute()} replace />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/:class_id/module/:module_id/project/:id/submissions" element={
                        <ProtectedRoute>
                            <Navigate to={redirectLegacyAdminRoute()} replace />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/:class_id/module/:module_id/project/:id/manage" element={
                        <ProtectedRoute>
                            <Navigate to={redirectLegacyAdminRoute()} replace />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/:class_id/module/:module_id/project/:project_id/grade/:id" element={
                        <ProtectedRoute>
                            <Navigate to={redirectLegacyAdminRoute()} replace />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/:class_id/module/:module_id/project/:project_id/codeview/:id" element={
                        <ProtectedRoute>
                            <Navigate to={redirectLegacyAdminRoute()} replace />
                        </ProtectedRoute>
                    } />

                    <Route path="/admin/:class_id/project/:id/overview" element={
                        <ProtectedRoute>
                            <Navigate to={redirectLegacyAdminRoute()} replace />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/:class_id/project/:id" element={
                        <ProtectedRoute>
                            <Navigate to={redirectLegacyAdminRoute()} replace />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/:class_id/project/:id/manage" element={
                        <ProtectedRoute>
                            <Navigate to={redirectLegacyAdminRoute()} replace />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/:class_id/project/:project_id/grade/:id" element={
                        <ProtectedRoute>
                            <Navigate to={redirectLegacyAdminRoute()} replace />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/:class_id/project/:project_id/codeview/:id" element={
                        <ProtectedRoute>
                            <AdminViewStudentCode />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/:class_id/project/:project_id/checkpoint/:checkpoint_id/codeview/:id" element={
                        <ProtectedRoute>
                            <AdminViewStudentCode />
                        </ProtectedRoute>
                    } />
                    <Route path="/admin/plagiarism" element={
                        <ProtectedRoute>
                            <AdminPlagiarism />
                        </ProtectedRoute>
                    } />
                    <Route path="/student/school/:school_id/classes" element={
                        <ProtectedRoute>
                            <StudentClassSelection />
                        </ProtectedRoute>
                    } />
                    <Route path="/student/school/:school_id/class/:class_id/modules/*" element={
                        <ProtectedRoute>
                            <StudentModuleList />
                        </ProtectedRoute>
                    } />
                    <Route path="/student/school/:school_id/class/:class_id/module/:module_id" element={
                        <ProtectedRoute>
                            <StudentModuleDetails />
                        </ProtectedRoute>
                    } />
                    <Route path="/student/school/:school_id/class/:class_id/module/:module_id/project/:project_id/upload" element={
                        <ProtectedRoute>
                            <StudentUpload />
                        </ProtectedRoute>
                    } />
                    <Route path="/student/school/:school_id/class/:class_id/module/:module_id/project/:project_id/checkpoint/:checkpoint_id/upload" element={
                        <ProtectedRoute>
                            <StudentUpload />
                        </ProtectedRoute>
                    } />

                    <Route path="/student/:class_id/upload" element={
                        <ProtectedRoute>
                            <StudentUpload />
                        </ProtectedRoute>
                    } />
                    <Route path="/student/:class_id/checkpoint/:checkpoint_id/upload" element={
                        <ProtectedRoute>
                            <StudentUpload />
                        </ProtectedRoute>
                    } />
                    <Route path="*" element={<NotFound />} />
                </Routes>
            </BrowserRouter>
        );
    }
}

export default App;
