  function [status,owner] = hw_stat(Action,io,name)
% function [status,owner] = hw_stat(Action,io,name)
% Central SigLab hardware clearing point
% Actions
%     'request'          request use of hardware
%     'free'             relinquish hardware
% 
%        io  =  'in'     request input subsystem
%        io  =  'out'    request output subsystem
%        io  =  'in&out' request both
%     'owners'           return owners
%     'clear'            unconditionally free both in and out 
%
% Responses : status 
%      not_avail   =  0;
%      u_own_it    =  1;
%      error       = -1;
%      its_free    =  1;
% Dick Benson DSP Technology

% v5 mods 11/16/95

% Responses : status
      not_avail   =  0;
      u_own_it    =  1;
      err         = -1;
      its_free    =  1;
      
      global HWSTAT;     % holds the names of the 'owners'
                         % [input_owner, output_owner]
      In  = 1;
      Out = 2;

      if strcmp(Action,'request')
            if sum(size(HWSTAT)) == 0,
                 % by definition all hw must be free
                 
                 if strcmp(io,'in'),
                     HWSTAT = put_str(2,name,'_');
                     status = u_own_it;
                     owner  = name;
                 elseif strcmp(io,'out'),
                     HWSTAT = put_str(2,'_',name);
                     status = u_own_it;
                     owner  = name;
                 elseif strcmp(io,'in&out'),
                     HWSTAT = put_str(2,name,name);
                     status = u_own_it;
                     owner  = name;
                 else    
                     error=[io, ' not recognized in hw_stat.m request 1']
                     status=err;
                     owner = '';
                 end;
                 
            elseif strcmp(io,'in'),
                   
                   if strcmp(deblank(HWSTAT(In,:)),'_'),
                      % input is not in use
                      status = u_own_it;
                      owner  = name;
                      HWSTAT = put_str(2,name,HWSTAT(Out,:));
                   else
                      % input in in use
                      status = not_avail;
                      owner  = deblank(HWSTAT(In,:)); 
                   end;
                 
            elseif  strcmp(io,'out'),
                   if strcmp(deblank(HWSTAT(Out,:)),'_'),
                      % output is not in use
                      status = u_own_it;
                      owner  = name;
                      HWSTAT = put_str(2,HWSTAT(In,:),name);
                   else
                      % output in in use
                      status = not_avail;
                      owner  = deblank(HWSTAT(Out,:)); 
                   end;          
       
            
            elseif  strcmp(io,'in&out'),
            
                   if strcmp(deblank(HWSTAT(In,:)),'_'),
                      % input is not in use
                      status = u_own_it;
                      owner  = name; 
                   else
                      % input already in in use
                      status = not_avail;
                      owner  = deblank(HWSTAT(In,:)); 
                   end;
                   
                   if status==u_own_it,
                     if strcmp(deblank(HWSTAT(Out,:)),'_'),
                        % output is not in use
                        status = u_own_it;
                        owner  = name; 
                     else
                        % output in in use
                        status = not_avail;
                        owner  = deblank(HWSTAT(Out,:)); 
                     end; 
                   end;
                   if status == u_own_it,
                       HWSTAT = put_str(2,name,name);
                   end;
            else
                error=[io, ' not recognized in hw_stat.m request 2']
            end;

      elseif strcmp(Action,'free'),
            if strcmp(io,'in'),
                 if strcmp(deblank(HWSTAT(In,:)),'_'),
                     status = its_free;
                     owner  = '';
                 
                 elseif strcmp(deblank(HWSTAT(In,:)),name), 
                     HWSTAT = put_str(2,'_',HWSTAT(Out,:));
                     status = its_free;
                     owner  = '';
                 else 
                     % can't free it if u don't own it
                     status = err;
                     owner  = deblank(HWSTAT(In,:));  
                 end;
            
            elseif strcmp(io,'out'),
                 if strcmp(deblank(HWSTAT(Out,:)),'_'),
                     status = its_free;
                     owner  = '';
                 elseif strcmp(deblank(HWSTAT(Out,:)),name), 
                     HWSTAT = put_str(2,HWSTAT(In,:),'_');
                     status = its_free;
                     owner  = '';
                 else 
                     % can't free it if u don't own it
                     status = err;
                     owner  = deblank(HWSTAT(Out,:));  
                 end;
            
            elseif strcmp(io,'in&out'),
                 if strcmp(deblank(HWSTAT(In,:)),'_'),
                     status = its_free;
                     owner  = '';
                 
                 elseif strcmp(deblank(HWSTAT(In,:)),name), 
                     HWSTAT = put_str(2,'_',HWSTAT(Out,:));
                     status = its_free;
                     owner  = '';
                 else 
                     % can't free it if u don't own it
                     status = err;
                     owner  = deblank(HWSTAT(In,:));  
                 end;
                 
                 if status == its_free,
                     if strcmp(deblank(HWSTAT(Out,:)),'_'),
                        status = its_free;
                        owner  = '';
                     elseif strcmp(deblank(HWSTAT(Out,:)),name), 
                        HWSTAT = put_str(2,HWSTAT(In,:),'_');
                        status = its_free;
                        owner  = '';
                     else 
                        % can't free it if u don't own it
                        status = err;
                        owner  = deblank(HWSTAT(Out,:));  
                     end; 
                 end; 
            else
               error=[io, ' not recognized in hw_stat.m free']
            end; 
            
      elseif strcmp(Action,'owners'),
            status = '';
            owner  = HWSTAT;
            
      elseif strcmp(Action,'clear'),
            HWSTAT = []; 
      else
          error=[Action,' not recognized in hw_stat.m']
      end; 
% end;  hw_stat








