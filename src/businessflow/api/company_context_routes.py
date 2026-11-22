from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from businessflow.api.dependencies import get_current_user, get_session
from businessflow.company_context.domain import PROFICIENCY_LEVELS
from businessflow.company_context.models import EmployeeWeeklyCapacity
from businessflow.company_context.schemas import (
    AssignmentStatus,
    BusinessUnitCreate,
    BusinessUnitPage,
    BusinessUnitResponse,
    BusinessUnitUpdate,
    CompanyContextSummary,
    DepartmentCreate,
    DepartmentPage,
    DepartmentResponse,
    DepartmentUpdate,
    EmployeeCreate,
    EmployeePage,
    EmployeeResponse,
    EmployeeSkillCreate,
    EmployeeSkillPage,
    EmployeeSkillResponse,
    EmployeeSkillUpdate,
    EmployeeUpdate,
    ProjectCreate,
    ProjectPage,
    ProjectResponse,
    ProjectStatus,
    ProjectUpdate,
    ResourceAssignmentCreate,
    ResourceAssignmentHistoryItem,
    ResourceAssignmentHistoryPage,
    ResourceAssignmentPage,
    ResourceAssignmentResponse,
    ResourceAssignmentUpdate,
    SkillCreate,
    SkillPage,
    SkillResponse,
    SkillUpdate,
    UnitStatus,
    WeeklyCapacityCreate,
    WeeklyCapacityPage,
    WeeklyCapacityResponse,
)
from businessflow.errors import ApiError, CommitAndRaise
from businessflow.models import Company, UserAccount
from businessflow.position_reviews.models import PositionDemoSeed
from businessflow.services.company_context_access import require_company_context_access
from businessflow.services.company_context_summary import company_context_summary
from businessflow.services.company_organization import (
    create_business_unit,
    create_department,
    get_business_unit,
    get_department,
    list_business_units,
    list_departments,
    update_business_unit,
    update_department,
)
from businessflow.services.company_projects import (
    cancel_resource_assignment,
    create_project,
    create_resource_assignment,
    deactivate_project,
    get_project,
    get_resource_assignment,
    list_projects,
    list_resource_assignment_history,
    list_resource_assignments,
    update_project,
    update_resource_assignment,
)
from businessflow.services.company_workforce import (
    EmployeeSkillView,
    EmployeeView,
    available_hours,
    create_employee,
    create_employee_skill,
    create_skill,
    deactivate_employee,
    deactivate_skill,
    get_employee,
    get_skill,
    list_employee_skills,
    list_employees,
    list_skills,
    list_weekly_capacity,
    list_weekly_capacity_history,
    record_weekly_capacity,
    remove_employee_skill,
    update_employee,
    update_employee_skill,
    update_skill,
)


def _protect_seeded_demo_workspace(
    request: Request,
    tenant_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    try:
        access = require_company_context_access(session, user, tenant_id, "company_context:write")
    except (ApiError, CommitAndRaise):
        # Preserve the endpoint's normal authorization and request-validation response. The
        # guard only adds the seeded-workspace restriction when this caller can otherwise write.
        return
    # Use the same Company-row lock as explicit demo seeding. A direct write that races with
    # seeding must either finish before the emptiness check or observe the committed seed.
    session.scalar(
        select(Company.id)
        .where(Company.id == access.company.id, Company.tenant_id == tenant_id)
        .with_for_update()
    )
    seed_exists = session.scalar(
        select(PositionDemoSeed.id).where(
            PositionDemoSeed.tenant_id == tenant_id,
            PositionDemoSeed.company_id == access.company.id,
        )
    )
    if seed_exists is not None:
        raise ApiError(
            409,
            "synthetic_demo_operational_api_read_only",
            (
                "M10 workspaces accept canonical operational writes only through their exact "
                "built-in synthetic M4 templates; direct record changes are disabled."
            ),
        )


router = APIRouter(
    prefix="/tenants/{tenant_id}",
    tags=["company context"],
    dependencies=[Depends(_protect_seeded_demo_workspace)],
)
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=100_000)]


def _employee_response(view: EmployeeView) -> EmployeeResponse:
    employee = view.employee
    return EmployeeResponse(
        id=employee.id,
        tenant_id=employee.tenant_id,
        company_id=employee.company_id,
        employee_code=employee.employee_code,
        full_name=employee.full_name,
        job_title=employee.job_title,
        department_id=employee.department_id,
        business_unit_id=view.business_unit_id,
        employment_status=employee.employment_status,
        employment_start_date=employee.employment_start_date,
        employment_end_date=employee.employment_end_date,
        standard_weekly_capacity_hours=employee.standard_weekly_capacity_hours,
        created_at=employee.created_at,
        updated_at=employee.updated_at,
    )


def _employee_skill_response(view: EmployeeSkillView) -> EmployeeSkillResponse:
    relation = view.employee_skill
    return EmployeeSkillResponse(
        id=relation.id,
        tenant_id=relation.tenant_id,
        company_id=relation.company_id,
        employee_id=relation.employee_id,
        skill_id=relation.skill_id,
        skill_name=view.skill_name,
        proficiency=relation.proficiency,
        proficiency_label=PROFICIENCY_LEVELS[relation.proficiency],
        status=relation.status,
        created_at=relation.created_at,
        updated_at=relation.updated_at,
    )


def _capacity_response(record: EmployeeWeeklyCapacity) -> WeeklyCapacityResponse:
    return WeeklyCapacityResponse(
        id=record.id,
        tenant_id=record.tenant_id,
        company_id=record.company_id,
        employee_id=record.employee_id,
        week_start_date=record.week_start_date,
        version=record.version,
        supersedes_version=record.supersedes_version,
        availability_status=record.availability_status,
        scheduled_hours=record.scheduled_hours,
        non_project_hours=record.non_project_hours,
        unavailable_hours=record.unavailable_hours,
        available_hours=available_hours(record),
        source=record.source,
        reason=record.reason,
        created_by_user_id=record.created_by_user_id,
        created_at=record.created_at,
    )


@router.get("/business-units", response_model=BusinessUnitPage)
def get_business_units(
    tenant_id: UUID,
    status: UnitStatus | None = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> BusinessUnitPage:
    rows, total = list_business_units(
        session, user, tenant_id, status=status, limit=limit, offset=offset
    )
    return BusinessUnitPage(
        items=[BusinessUnitResponse.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/business-units", response_model=BusinessUnitResponse, status_code=201)
def post_business_unit(
    tenant_id: UUID,
    body: BusinessUnitCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> BusinessUnitResponse:
    return BusinessUnitResponse.model_validate(
        create_business_unit(session, user, tenant_id, body, request.state.request_id)
    )


@router.get("/business-units/{unit_id}", response_model=BusinessUnitResponse)
def read_business_unit(
    tenant_id: UUID,
    unit_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> BusinessUnitResponse:
    return BusinessUnitResponse.model_validate(get_business_unit(session, user, tenant_id, unit_id))


@router.patch("/business-units/{unit_id}", response_model=BusinessUnitResponse)
def patch_business_unit(
    tenant_id: UUID,
    unit_id: UUID,
    body: BusinessUnitUpdate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> BusinessUnitResponse:
    return BusinessUnitResponse.model_validate(
        update_business_unit(session, user, tenant_id, unit_id, body, request.state.request_id)
    )


@router.delete("/business-units/{unit_id}", status_code=204)
def delete_business_unit(
    tenant_id: UUID,
    unit_id: UUID,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Response:
    update_business_unit(
        session,
        user,
        tenant_id,
        unit_id,
        BusinessUnitUpdate(status="inactive"),
        request.state.request_id,
    )
    return Response(status_code=204)


@router.get("/departments", response_model=DepartmentPage)
def get_departments(
    tenant_id: UUID,
    business_unit_id: UUID | None = None,
    status: UnitStatus | None = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> DepartmentPage:
    rows, total = list_departments(
        session,
        user,
        tenant_id,
        business_unit_id=business_unit_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return DepartmentPage(
        items=[DepartmentResponse.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/departments", response_model=DepartmentResponse, status_code=201)
def post_department(
    tenant_id: UUID,
    body: DepartmentCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> DepartmentResponse:
    return DepartmentResponse.model_validate(
        create_department(session, user, tenant_id, body, request.state.request_id)
    )


@router.get("/departments/{department_id}", response_model=DepartmentResponse)
def read_department(
    tenant_id: UUID,
    department_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> DepartmentResponse:
    return DepartmentResponse.model_validate(
        get_department(session, user, tenant_id, department_id)
    )


@router.patch("/departments/{department_id}", response_model=DepartmentResponse)
def patch_department(
    tenant_id: UUID,
    department_id: UUID,
    body: DepartmentUpdate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> DepartmentResponse:
    return DepartmentResponse.model_validate(
        update_department(session, user, tenant_id, department_id, body, request.state.request_id)
    )


@router.delete("/departments/{department_id}", status_code=204)
def delete_department(
    tenant_id: UUID,
    department_id: UUID,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Response:
    update_department(
        session,
        user,
        tenant_id,
        department_id,
        DepartmentUpdate(status="inactive"),
        request.state.request_id,
    )
    return Response(status_code=204)


@router.get("/employees", response_model=EmployeePage)
def get_employees(
    tenant_id: UUID,
    status: Literal["active", "on_leave", "inactive", "terminated"] | None = None,
    department_id: UUID | None = None,
    business_unit_id: UUID | None = None,
    skill_id: UUID | None = None,
    sort_by: Literal[
        "employee_code", "full_name", "created_at", "employment_status"
    ] = "employee_code",
    descending: bool = False,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> EmployeePage:
    rows, total = list_employees(
        session,
        user,
        tenant_id,
        status=status,
        department_id=department_id,
        business_unit_id=business_unit_id,
        skill_id=skill_id,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        descending=descending,
    )
    return EmployeePage(
        items=[_employee_response(row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.post("/employees", response_model=EmployeeResponse, status_code=201)
def post_employee(
    tenant_id: UUID,
    body: EmployeeCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> EmployeeResponse:
    return _employee_response(
        create_employee(session, user, tenant_id, body, request.state.request_id)
    )


@router.get("/employees/{employee_id}", response_model=EmployeeResponse)
def read_employee(
    tenant_id: UUID,
    employee_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> EmployeeResponse:
    return _employee_response(get_employee(session, user, tenant_id, employee_id))


@router.patch("/employees/{employee_id}", response_model=EmployeeResponse)
def patch_employee(
    tenant_id: UUID,
    employee_id: UUID,
    body: EmployeeUpdate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> EmployeeResponse:
    return _employee_response(
        update_employee(session, user, tenant_id, employee_id, body, request.state.request_id)
    )


@router.delete("/employees/{employee_id}", status_code=204)
def delete_employee(
    tenant_id: UUID,
    employee_id: UUID,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Response:
    deactivate_employee(session, user, tenant_id, employee_id, request.state.request_id)
    return Response(status_code=204)


@router.get("/skills", response_model=SkillPage)
def get_skills(
    tenant_id: UUID,
    status: UnitStatus | None = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> SkillPage:
    rows, total = list_skills(session, user, tenant_id, status=status, limit=limit, offset=offset)
    return SkillPage(
        items=[SkillResponse.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/skills", response_model=SkillResponse, status_code=201)
def post_skill(
    tenant_id: UUID,
    body: SkillCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> SkillResponse:
    return SkillResponse.model_validate(
        create_skill(session, user, tenant_id, body, request.state.request_id)
    )


@router.get("/skills/{skill_id}", response_model=SkillResponse)
def read_skill(
    tenant_id: UUID,
    skill_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> SkillResponse:
    return SkillResponse.model_validate(get_skill(session, user, tenant_id, skill_id))


@router.patch("/skills/{skill_id}", response_model=SkillResponse)
def patch_skill(
    tenant_id: UUID,
    skill_id: UUID,
    body: SkillUpdate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> SkillResponse:
    return SkillResponse.model_validate(
        update_skill(session, user, tenant_id, skill_id, body, request.state.request_id)
    )


@router.delete("/skills/{skill_id}", status_code=204)
def delete_skill(
    tenant_id: UUID,
    skill_id: UUID,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Response:
    deactivate_skill(session, user, tenant_id, skill_id, request.state.request_id)
    return Response(status_code=204)


@router.get("/employees/{employee_id}/skills", response_model=EmployeeSkillPage)
def get_employee_skills(
    tenant_id: UUID,
    employee_id: UUID,
    status: UnitStatus | None = "active",
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> EmployeeSkillPage:
    rows, total = list_employee_skills(
        session,
        user,
        tenant_id,
        employee_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return EmployeeSkillPage(
        items=[_employee_skill_response(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/employees/{employee_id}/skills",
    response_model=EmployeeSkillResponse,
    status_code=201,
)
def post_employee_skill(
    tenant_id: UUID,
    employee_id: UUID,
    body: EmployeeSkillCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> EmployeeSkillResponse:
    return _employee_skill_response(
        create_employee_skill(session, user, tenant_id, employee_id, body, request.state.request_id)
    )


@router.patch("/employees/{employee_id}/skills/{skill_id}", response_model=EmployeeSkillResponse)
def patch_employee_skill(
    tenant_id: UUID,
    employee_id: UUID,
    skill_id: UUID,
    body: EmployeeSkillUpdate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> EmployeeSkillResponse:
    return _employee_skill_response(
        update_employee_skill(
            session, user, tenant_id, employee_id, skill_id, body, request.state.request_id
        )
    )


@router.delete("/employees/{employee_id}/skills/{skill_id}", status_code=204)
def delete_employee_skill(
    tenant_id: UUID,
    employee_id: UUID,
    skill_id: UUID,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Response:
    remove_employee_skill(session, user, tenant_id, employee_id, skill_id, request.state.request_id)
    return Response(status_code=204)


@router.post(
    "/employees/{employee_id}/weekly-capacity/{week_start_date}",
    response_model=WeeklyCapacityResponse,
    status_code=201,
)
def post_weekly_capacity(
    tenant_id: UUID,
    employee_id: UUID,
    week_start_date: date,
    body: WeeklyCapacityCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> WeeklyCapacityResponse:
    row = record_weekly_capacity(
        session, user, tenant_id, employee_id, week_start_date, body, request.state.request_id
    )
    return _capacity_response(row)


@router.get("/employees/{employee_id}/weekly-capacity", response_model=WeeklyCapacityPage)
def get_weekly_capacity(
    tenant_id: UUID,
    employee_id: UUID,
    start_week: date | None = None,
    end_week: date | None = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> WeeklyCapacityPage:
    rows, total = list_weekly_capacity(
        session,
        user,
        tenant_id,
        employee_id,
        start_week=start_week,
        end_week=end_week,
        limit=limit,
        offset=offset,
    )
    return WeeklyCapacityPage(
        items=[_capacity_response(row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.get(
    "/employees/{employee_id}/weekly-capacity/{week_start_date}/history",
    response_model=WeeklyCapacityPage,
)
def get_weekly_capacity_history(
    tenant_id: UUID,
    employee_id: UUID,
    week_start_date: date,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> WeeklyCapacityPage:
    rows, total = list_weekly_capacity_history(
        session,
        user,
        tenant_id,
        employee_id,
        week_start_date,
        limit=limit,
        offset=offset,
    )
    return WeeklyCapacityPage(
        items=[_capacity_response(row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.get("/projects", response_model=ProjectPage)
def get_projects(
    tenant_id: UUID,
    status: ProjectStatus | None = None,
    manager_employee_id: UUID | None = None,
    start_after: date | None = None,
    end_before: date | None = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ProjectPage:
    rows, total = list_projects(
        session,
        user,
        tenant_id,
        status=status,
        manager_employee_id=manager_employee_id,
        start_after=start_after,
        end_before=end_before,
        limit=limit,
        offset=offset,
    )
    return ProjectPage(
        items=[ProjectResponse.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/projects", response_model=ProjectResponse, status_code=201)
def post_project(
    tenant_id: UUID,
    body: ProjectCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ProjectResponse:
    return ProjectResponse.model_validate(
        create_project(session, user, tenant_id, body, request.state.request_id)
    )


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def read_project(
    tenant_id: UUID,
    project_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ProjectResponse:
    return ProjectResponse.model_validate(get_project(session, user, tenant_id, project_id))


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
def patch_project(
    tenant_id: UUID,
    project_id: UUID,
    body: ProjectUpdate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ProjectResponse:
    return ProjectResponse.model_validate(
        update_project(session, user, tenant_id, project_id, body, request.state.request_id)
    )


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(
    tenant_id: UUID,
    project_id: UUID,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Response:
    deactivate_project(session, user, tenant_id, project_id, request.state.request_id)
    return Response(status_code=204)


@router.get("/projects/{project_id}/assignments", response_model=ResourceAssignmentPage)
def get_project_assignments(
    tenant_id: UUID,
    project_id: UUID,
    status: AssignmentStatus | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ResourceAssignmentPage:
    get_project(session, user, tenant_id, project_id)
    rows, total = list_resource_assignments(
        session,
        user,
        tenant_id,
        project_id=project_id,
        employee_id=None,
        status=status,
        period_start=period_start,
        period_end=period_end,
        limit=limit,
        offset=offset,
    )
    return ResourceAssignmentPage(
        items=[ResourceAssignmentResponse.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/projects/{project_id}/assignments",
    response_model=ResourceAssignmentResponse,
    status_code=201,
)
def post_project_assignment(
    tenant_id: UUID,
    project_id: UUID,
    body: ResourceAssignmentCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ResourceAssignmentResponse:
    return ResourceAssignmentResponse.model_validate(
        create_resource_assignment(
            session, user, tenant_id, project_id, body, request.state.request_id
        )
    )


@router.get("/resource-assignments", response_model=ResourceAssignmentPage)
def get_resource_assignments(
    tenant_id: UUID,
    project_id: UUID | None = None,
    employee_id: UUID | None = None,
    status: AssignmentStatus | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ResourceAssignmentPage:
    rows, total = list_resource_assignments(
        session,
        user,
        tenant_id,
        project_id=project_id,
        employee_id=employee_id,
        status=status,
        period_start=period_start,
        period_end=period_end,
        limit=limit,
        offset=offset,
    )
    return ResourceAssignmentPage(
        items=[ResourceAssignmentResponse.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/resource-assignments/{assignment_id}", response_model=ResourceAssignmentResponse)
def read_resource_assignment(
    tenant_id: UUID,
    assignment_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ResourceAssignmentResponse:
    return ResourceAssignmentResponse.model_validate(
        get_resource_assignment(session, user, tenant_id, assignment_id)
    )


@router.patch("/resource-assignments/{assignment_id}", response_model=ResourceAssignmentResponse)
def patch_resource_assignment(
    tenant_id: UUID,
    assignment_id: UUID,
    body: ResourceAssignmentUpdate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ResourceAssignmentResponse:
    return ResourceAssignmentResponse.model_validate(
        update_resource_assignment(
            session, user, tenant_id, assignment_id, body, request.state.request_id
        )
    )


@router.delete("/resource-assignments/{assignment_id}", status_code=204)
def delete_resource_assignment(
    tenant_id: UUID,
    assignment_id: UUID,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Response:
    cancel_resource_assignment(session, user, tenant_id, assignment_id, request.state.request_id)
    return Response(status_code=204)


@router.get(
    "/resource-assignments/{assignment_id}/history",
    response_model=ResourceAssignmentHistoryPage,
)
def get_resource_assignment_history(
    tenant_id: UUID,
    assignment_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ResourceAssignmentHistoryPage:
    rows, total = list_resource_assignment_history(
        session, user, tenant_id, assignment_id, limit=limit, offset=offset
    )
    return ResourceAssignmentHistoryPage(
        items=[
            ResourceAssignmentHistoryItem(
                id=row.id,
                event_type=row.event_type,
                actor_user_id=row.actor_user_id,
                created_at=row.created_at,
                details=row.details,
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/company-context", response_model=CompanyContextSummary)
def get_company_context_summary(
    tenant_id: UUID,
    week_start_date: date | None = None,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CompanyContextSummary:
    return company_context_summary(session, user, tenant_id, week_start_date=week_start_date)
